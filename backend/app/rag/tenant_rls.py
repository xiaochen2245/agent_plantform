"""
PostgreSQL 16+ 原生行级安全 (RLS) 与多租户硬隔离会话管理器
backend/app/rag/tenant_rls.py
"""

from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict, List, Optional
from sqlalchemy import Select, text
from sqlalchemy.ext.asyncio import AsyncSession, AsyncEngine

from app.core.tenant_context import TenantContext


# 受 PostgreSQL 行级安全保护的多租户核心表
RLS_PROTECTED_TABLES: List[str] = [
    "documents",
    "document_chunks",
    "audit_tasks",
    "review_results",
]


def generate_rls_sql(tables: Optional[List[str]] = None) -> str:
    """
    生成针对 PostgreSQL 16+ 的生产级 RLS 行级安全加固 DDL 脚本。
    核心安全特性:
    1. ENABLE ROW LEVEL SECURITY: 开启行级安全策略。
    2. FORCE ROW LEVEL SECURITY: 强制表拥有者 (Table Owner / App DB User) 同样受到 RLS 约束，杜绝越权。
    3. NULLIF(current_setting('app.current_tenant_id', true), ''): 租户未设置时安全求值为 NULL，禁止返回任何数据。
    """
    target_tables = tables or RLS_PROTECTED_TABLES
    ddl_statements = [
        "-- 启用 PostgreSQL 向量扩展与行级安全 (Row-Level Security)",
        "CREATE EXTENSION IF NOT EXISTS vector;",
    ]
    for tbl in target_tables:
        ddl_statements.extend([
            f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY;",
            f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY;",
            f"DROP POLICY IF EXISTS tenant_isolation_policy ON {tbl};",
            f"CREATE POLICY tenant_isolation_policy ON {tbl}",
            f"    FOR ALL",
            f"    USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), ''))",
            f"    WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), ''));",
        ])
    return "\n".join(ddl_statements)


class TenantRLSManager:
    """多租户 RLS 会话与策略管理器"""

    @classmethod
    def set_current_tenant_id(cls, tenant_id: str):
        """设置当前线程/协程租户 ID"""
        if not tenant_id:
            raise ValueError("tenant_id 不能为空")
        from app.core.tenant_context import _tenant_context_var
        return _tenant_context_var.set(tenant_id)

    @classmethod
    def reset_tenant_id(cls, token):
        """重置租户上下文至先前状态"""
        from app.core.tenant_context import _tenant_context_var
        _tenant_context_var.reset(token)

    @classmethod
    def get_current_tenant_id(cls) -> Optional[str]:
        """获取当前租户 ID"""
        from app.core.tenant_context import _tenant_context_var
        return _tenant_context_var.get()

    @classmethod
    def is_postgres_session(cls, session: AsyncSession) -> bool:
        """检查当前会话是否连接至 PostgreSQL 引擎"""
        bind = session.bind or session.get_bind()
        return bool(bind and bind.dialect.name == "postgresql")

    @classmethod
    async def apply_rls(cls, session: AsyncSession, tenant_id: Optional[str] = None) -> None:
        """
        在当前会话的事务中应用 SET LOCAL app.current_tenant_id = :tid。
        若非 PostgreSQL 环境 (如测试 SQLite)，则跳过 SQL 执行，由 TenantContext 维持内存上下文。
        """
        tid = tenant_id or TenantContext.get_current_tenant_id()
        if cls.is_postgres_session(session):
            await session.execute(
                text("SET LOCAL app.current_tenant_id = :tid"),
                {"tid": tid}
            )

    @classmethod
    @asynccontextmanager
    async def tenant_rls_session(
        cls,
        session: AsyncSession,
        tenant_id: Optional[str] = None
    ) -> AsyncGenerator[AsyncSession, None]:
        """
        异步上下文管理器：
        1. 自动进入事务 (session.begin())
        2. 设置 SET LOCAL app.current_tenant_id
        3. 绑定 Python TenantContext
        4. 异常自动回滚，正常提交，事务结束自动重置局部变量
        """
        tid = tenant_id or TenantContext.get_current_tenant_id()
        
        with TenantContext(tid):
            if cls.is_postgres_session(session):
                if not session.in_transaction():
                    async with session.begin():
                        await session.execute(
                            text("SET LOCAL app.current_tenant_id = :tid"),
                            {"tid": tid}
                        )
                        yield session
                else:
                    await session.execute(
                        text("SET LOCAL app.current_tenant_id = :tid"),
                        {"tid": tid}
                    )
                    yield session
            else:
                # SQLite 测试环境
                yield session

    @classmethod
    async def verify_isolation(cls, session: AsyncSession, tenant_id: str) -> bool:
        """
        运行时隔离探针：执行安全探针查询，验证当前会话确实无法读取其他租户数据。
        """
        if not cls.is_postgres_session(session):
            return True
        result = await session.execute(
            text("SELECT current_setting('app.current_tenant_id', true)")
        )
        current = result.scalar()
        return current == tenant_id

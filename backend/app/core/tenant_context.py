"""
多租户安全上下文管理器 (TenantContext)
基于 Python contextvars 实现线程/协程安全的租户绑定
支持:
1. 在 FastAPI 请求生命周期自动绑定/注销 tenant_id
2. 提供查询自动注入 tenant_id 过滤的辅助工具
3. 配合 PostgreSQL 连接池执行 SET LOCAL app.current_tenant_id 触发 RLS
"""

from contextvars import ContextVar
from typing import Optional
from sqlalchemy import Select, select, text
from sqlalchemy.ext.asyncio import AsyncSession

# 全局协程隔离的当前租户 ID
_tenant_context_var: ContextVar[Optional[str]] = ContextVar("current_tenant_id", default=None)


class TenantContext:
    """租户上下文管理器 (支持同步与异步 with 上下文)"""

    def __init__(self, tenant_id: str):
        if not tenant_id:
            raise ValueError("tenant_id 不能为空")
        self.tenant_id = tenant_id
        self._token = None

    def __enter__(self):
        self._token = _tenant_context_var.set(self.tenant_id)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._token is not None:
            _tenant_context_var.reset(self._token)

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.__exit__(exc_type, exc_val, exc_tb)

    @classmethod
    def get_current_tenant_id(cls) -> str:
        """获取当前协程绑定的租户 ID，未设置则抛出安全越权异常"""
        tenant_id = _tenant_context_var.get()
        if not tenant_id:
            raise PermissionError("未设置租户上下文，严禁在脱离 tenant_id 隔离环境下访问多租户数据！")
        return tenant_id

    @classmethod
    def get_current_tenant_id_optional(cls) -> Optional[str]:
        """获取当前租户ID (允许为空)"""
        return _tenant_context_var.get()


def is_postgres_session(session: AsyncSession) -> bool:
    """
    检查当前会话是否连接至 PostgreSQL 引擎。
    具备完全防御性：安全处理未绑定会话、Mock 会话以及非 PostgreSQL 数据库方言。
    """
    try:
        bind = getattr(session, "bind", None)
        if bind is None and hasattr(session, "get_bind"):
            bind = session.get_bind()
        return bool(bind and hasattr(bind, "dialect") and bind.dialect.name == "postgresql")
    except Exception:
        return False


async def apply_tenant_rls_session(session: AsyncSession, tenant_id: Optional[str] = None) -> None:
    """
    在当前异步 Session 事务中设置 PostgreSQL 运行时变量，触发数据库原生 RLS。
    采用参数化安全函数调用：SELECT set_config('app.current_tenant_id', :tid, true)。
    若为 SQLite 等非 PostgreSQL 环境，则安全跳过，由 TenantContext 维持应用层内存隔离。
    """
    if not is_postgres_session(session):
        return
    tid = tenant_id or TenantContext.get_current_tenant_id()
    await session.execute(
        text("SELECT set_config('app.current_tenant_id', :tid, true)"),
        {"tid": tid},
    )


def filter_by_tenant(statement: Select, model_cls) -> Select:
    """
    针对给定的 SQLAlchemy 查询语句，强制追加当前租户过滤条件
    """
    tenant_id = TenantContext.get_current_tenant_id()
    return statement.where(model_cls.tenant_id == tenant_id)

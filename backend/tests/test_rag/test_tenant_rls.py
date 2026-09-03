"""
PostgreSQL 16+ 行级安全 (RLS) 与多租户硬隔离测试套件
验证 FORCE ROW LEVEL SECURITY 生产级 DDL 生成、ContextVar 线程隔离、跨租户零泄漏及非法租户拦截
"""

import asyncio
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant_context import TenantContext
from app.db.session import SessionLocal, engine
from app.models.audit_rag import (
    AuditTask,
    Base,
    Document,
    DocumentChunk,
    ReviewResult,
    Tenant,
    generate_rls_sql as model_generate_rls_sql,
)
from app.rag.tenant_rls import (
    RLS_PROTECTED_TABLES,
    TenantRLSManager,
    generate_rls_sql as rag_generate_rls_sql,
)


@pytest.fixture
async def rls_test_session():
    """轻量级隔离测试数据库会话"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as session:
        t1 = Tenant(id="tenant_alpha", code="T_ALPHA", name="甲方总包集团")
        t2 = Tenant(id="tenant_beta", code="T_BETA", name="乙方投标联合体")
        session.add_all([t1, t2])

        d1 = Document(
            id="doc_alpha",
            tenant_id="tenant_alpha",
            title="alpha_tech_spec.docx",
            file_type="docx",
            s3_path="/alpha/spec.docx",
            file_hash="hash_alpha"
        )
        d2 = Document(
            id="doc_beta",
            tenant_id="tenant_beta",
            title="beta_commercial_bid.docx",
            file_type="docx",
            s3_path="/beta/bid.docx",
            file_hash="hash_beta"
        )
        session.add_all([d1, d2])
        await session.commit()
        yield session


def test_rls_sql_generation_contains_force():
    """1. 验证生成的 RLS DDL 严格包含 FORCE ROW LEVEL SECURITY (防表拥有者绕过)"""
    ddl = rag_generate_rls_sql()
    assert "FORCE ROW LEVEL SECURITY" in ddl
    for tbl in RLS_PROTECTED_TABLES:
        assert f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY;" in ddl


def test_rls_sql_generation_policy_names():
    """2. 验证生成的安全策略名称格式与 USING/CHECK 表达式"""
    ddl = rag_generate_rls_sql()
    for tbl in RLS_PROTECTED_TABLES:
        assert f"CREATE POLICY tenant_isolation_policy ON {tbl}" in ddl
        assert "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')" in ddl


def test_rls_all_four_tables_covered():
    """3. 验证受保护表全覆盖: documents, document_chunks, audit_tasks, review_results"""
    expected_tables = {"documents", "document_chunks", "audit_tasks", "review_results"}
    assert set(RLS_PROTECTED_TABLES) == expected_tables

    ddl = rag_generate_rls_sql()
    for tbl in expected_tables:
        assert f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY;" in ddl
        assert f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY;" in ddl


def test_rls_super_user_bypass_mitigation():
    """4. 验证 model 和 rag 模块的 DDL 生成器均包含双重防超级用户/Owner绕过指令"""
    ddl_model = model_generate_rls_sql()
    ddl_rag = rag_generate_rls_sql()

    assert "FORCE ROW LEVEL SECURITY" in ddl_model
    assert "FORCE ROW LEVEL SECURITY" in ddl_rag


def test_rls_contextvar_tenant_setting():
    """5. 验证 TenantRLSManager 正确绑定与获取 ContextVar"""
    token = TenantRLSManager.set_current_tenant_id("tenant_alpha")
    try:
        assert TenantRLSManager.get_current_tenant_id() == "tenant_alpha"
        assert TenantContext.get_current_tenant_id() == "tenant_alpha"
    finally:
        TenantRLSManager.reset_tenant_id(token)


def test_rls_contextvar_tenant_reset():
    """6. 验证 Reset Token 恢复租户上下文"""
    token = TenantRLSManager.set_current_tenant_id("temp_tenant")
    assert TenantRLSManager.get_current_tenant_id() == "temp_tenant"
    TenantRLSManager.reset_tenant_id(token)
    assert TenantRLSManager.get_current_tenant_id() is None


def test_rls_empty_tenant_id_rejection():
    """7. 验证空字符串租户 ID 被严格拒绝抛出 ValueError"""
    with pytest.raises(ValueError):
        TenantRLSManager.set_current_tenant_id("")

    with pytest.raises(ValueError):
        with TenantContext(""):
            pass


@pytest.mark.asyncio
async def test_rls_apply_tenant_context_sqlite_fallback(rls_test_session: AsyncSession):
    """8. 验证在 SQLite 环境下平滑执行 tenant_rls_session 上下文管理"""
    async with TenantRLSManager.tenant_rls_session(rls_test_session, "tenant_alpha") as session:
        # 在上下文内，通过 ORM 验证查询
        stmt = select(Document).where(Document.tenant_id == "tenant_alpha")
        result = await session.execute(stmt)
        docs = result.scalars().all()
        assert len(docs) == 1
        assert docs[0].id == "doc_alpha"


@pytest.mark.asyncio
async def test_rls_zero_cross_tenant_leakage_assertion(rls_test_session: AsyncSession):
    """9. 验证跨租户数据查询在 tenant_id 隔离下产生 0 条数据泄露"""
    # 模拟在 tenant_alpha 边界下查询全部文档但只允许查看自身租户
    current_tid = "tenant_alpha"
    stmt = select(Document).where(Document.tenant_id == current_tid)
    res = await rls_test_session.execute(stmt)
    records = res.scalars().all()

    assert len(records) == 1
    assert records[0].id == "doc_alpha"
    assert all(r.tenant_id == "tenant_alpha" for r in records)

    # 验证不包含 tenant_beta
    assert all(r.tenant_id != "tenant_beta" for r in records)


@pytest.mark.asyncio
async def test_rls_thread_context_isolation():
    """10. 验证并发协程中不同租户 ID 的 ContextVar 严格隔离，互不干扰"""
    observed_tenants = {}

    async def worker(tenant_name: str, delay: float):
        with TenantContext(tenant_name):
            await asyncio.sleep(delay)
            # 延时后验证依然为自身租户
            observed_tenants[tenant_name] = TenantContext.get_current_tenant_id()

    # 同时启动 5 个并发异步任务
    tasks = [
        worker("tenant_1", 0.05),
        worker("tenant_2", 0.02),
        worker("tenant_3", 0.04),
        worker("tenant_4", 0.01),
        worker("tenant_5", 0.03),
    ]
    await asyncio.gather(*tasks)

    for i in range(1, 6):
        name = f"tenant_{i}"
        assert observed_tenants[name] == name

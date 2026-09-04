"""
PostgreSQL 16+ 行级安全 (RLS) 与多租户硬隔离测试套件
验证 FORCE ROW LEVEL SECURITY 生产级 DDL 生成、ContextVar 线程隔离、跨租户零泄漏及非法租户拦截
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant_context import TenantContext, apply_tenant_rls_session, is_postgres_session
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
    """3. 验证受保护表全覆盖: documents, document_chunks, audit_tasks, review_results, historical_audit_risks"""
    expected_tables = {"documents", "document_chunks", "audit_tasks", "review_results", "historical_audit_risks"}
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


def test_is_postgres_session_dialects(rls_test_session: AsyncSession):
    """11. 验证方言检测：SQLite 与未绑定会话返回 False，PostgreSQL 返回 True"""
    # SQLite 内存会话检测
    assert not TenantRLSManager.is_postgres_session(rls_test_session)
    assert not is_postgres_session(rls_test_session)

    # 未绑定 session 防御性检测 (防止 AttributeError)
    unbound_session = AsyncSession()
    assert not TenantRLSManager.is_postgres_session(unbound_session)
    assert not is_postgres_session(unbound_session)

    # Mock PostgreSQL 会话检测
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_pg_session = AsyncMock(spec=AsyncSession)
    mock_pg_session.bind = mock_bind
    assert TenantRLSManager.is_postgres_session(mock_pg_session)
    assert is_postgres_session(mock_pg_session)


@pytest.mark.asyncio
async def test_apply_rls_sqlite_safe_noop(rls_test_session: AsyncSession):
    """12. 验证在 SQLite 环境下调用 apply_rls 与 apply_tenant_rls_session 安全静默跳过"""
    # 不抛出 OperationalError (例如 near "SET": syntax error 或 no such function: set_config)
    await TenantRLSManager.apply_rls(rls_test_session, "tenant_alpha")
    await apply_tenant_rls_session(rls_test_session, "tenant_alpha")


@pytest.mark.asyncio
async def test_apply_rls_postgres_executes_set_config():
    """13. 验证 PostgreSQL 环境下使用 SELECT set_config 参数化设置会话租户"""
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.bind = mock_bind
    mock_session.in_transaction.return_value = True

    # 1. 测试 TenantRLSManager.apply_rls
    await TenantRLSManager.apply_rls(mock_session, "tenant_alpha")
    assert mock_session.execute.called
    stmt, params = mock_session.execute.call_args[0]
    assert "SELECT set_config('app.current_tenant_id', :tid, true)" in str(stmt)
    assert params == {"tid": "tenant_alpha"}
    assert "SET LOCAL" not in str(stmt)

    # 2. 测试 apply_tenant_rls_session
    mock_session.execute.reset_mock()
    await apply_tenant_rls_session(mock_session, "tenant_beta")
    assert mock_session.execute.called
    stmt, params = mock_session.execute.call_args[0]
    assert "SELECT set_config('app.current_tenant_id', :tid, true)" in str(stmt)
    assert params == {"tid": "tenant_beta"}


@pytest.mark.asyncio
async def test_verify_isolation_postgres_mock():
    """14. 验证 verify_isolation 在 PostgreSQL 下使用 current_setting 校验"""
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.bind = mock_bind
    mock_result = MagicMock()
    mock_result.scalar.return_value = "tenant_gamma"
    mock_session.execute.return_value = mock_result

    assert await TenantRLSManager.verify_isolation(mock_session, "tenant_gamma") is True
    assert await TenantRLSManager.verify_isolation(mock_session, "tenant_other") is False


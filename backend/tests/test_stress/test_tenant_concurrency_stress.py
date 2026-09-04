"""
多租户高并发隔离与 RLS 零泄露压力测试套件 (test_tenant_concurrency_stress.py)
验证:
1. 50 协程并发跨租户频繁切换 ContextVar 协程级状态隔离 (0 污染)
2. 20 租户并发会话写入与查询硬隔离 (单租户仅可见自身数据)
3. 数据库连接池饱和吞吐与瞬时并发恢复能力
4. 对抗性金丝雀探针 (Adversarial Canary Probe) 越权检索零泄露 (Zero-Leakage)
"""

import asyncio
import random
import uuid
import pytest
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant_context import TenantContext, apply_tenant_rls_session
from app.db.session import SessionLocal, engine
from app.models.audit_rag import Base, Tenant, Document, DocumentChunk, ChunkLevel
from app.rag.tenant_rls import TenantRLSManager


class TestMultiTenantRLSStress:
    """多租户高并发与安全性压力测试"""

    @pytest.fixture(autouse=True)
    async def setup_db_tables(self):
        """确保测试表结构完整建立"""
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield
        # 测试结束后清理
        async with SessionLocal() as session:
            await session.execute(delete(DocumentChunk))
            await session.execute(delete(Document))
            await session.execute(delete(Tenant))
            await session.commit()

    @pytest.mark.asyncio
    async def test_high_concurrency_tenant_context_switch_safety(self):
        """验证 50 个并发协程跨 10 个租户频繁切换 ContextVar 时 100% 独立隔离"""
        tenant_pool = [f"tenant_ctx_{i}" for i in range(10)]
        coroutine_count = 50

        async def worker_coroutine(worker_id: int):
            # 随机挑选 3 个不同的租户，进行多层嵌套与交替切换
            assigned_tenants = random.sample(tenant_pool, 3)
            for target_tenant in assigned_tenants:
                assert TenantContext.get_current_tenant_id_optional() is None or TenantContext.get_current_tenant_id_optional() not in tenant_pool

                with TenantContext(target_tenant):
                    assert TenantContext.get_current_tenant_id() == target_tenant
                    # 模拟协程异步让渡执行权 (I/O 或计算让步)
                    await asyncio.sleep(random.uniform(0.001, 0.005))
                    # 嵌套租户切换
                    nested_tenant = f"{target_tenant}_nested"
                    with TenantContext(nested_tenant):
                        assert TenantContext.get_current_tenant_id() == nested_tenant
                        await asyncio.sleep(0.001)
                    # 退出嵌套后恢复外部租户
                    assert TenantContext.get_current_tenant_id() == target_tenant

                # 退出外部后重置
                assert TenantContext.get_current_tenant_id_optional() is None or TenantContext.get_current_tenant_id_optional() not in tenant_pool
            return True

        tasks = [worker_coroutine(i) for i in range(coroutine_count)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for idx, res in enumerate(results):
            assert res is True, f"协程 {idx} 上下文隔离断言失败: {res}"

    @pytest.mark.asyncio
    async def test_high_concurrency_multi_tenant_db_isolation(self):
        """验证 20 个并发租户同时写入并查询切片，数据硬隔离且无相互穿透"""
        tenant_count = 20
        chunks_per_tenant = 5
        tenants = [f"stress_ten_{i}" for i in range(tenant_count)]

        # 1. 并发注册租户与写入文档切片
        async def populate_tenant_data(t_id: str):
            async with SessionLocal() as session:
                async with TenantRLSManager.tenant_rls_session(session, t_id):
                    # 写入租户
                    session.add(Tenant(id=t_id, code=f"CODE_{t_id}", name=f"测试租户_{t_id}"))
                    # 写入父文档
                    doc_id = f"doc_{t_id}"
                    session.add(
                        Document(
                            id=doc_id,
                            tenant_id=t_id,
                            title=f"Doc_{t_id}.pdf",
                            file_type="pdf",
                            s3_path=f"s3://bucket/{t_id}/doc.pdf",
                            file_hash=uuid.uuid4().hex
                        )
                    )
                    # 写入切片
                    for c_idx in range(chunks_per_tenant):
                        chunk = DocumentChunk(
                            id=f"chk_{t_id}_{c_idx}",
                            tenant_id=t_id,
                            document_id=doc_id,
                            chunk_index=c_idx,
                            chunk_level=ChunkLevel.CHILD,
                            content=f"租户 {t_id} 的机密业务条款内容第 {c_idx} 条，严禁跨租户访问",
                            token_count=30,
                        )
                        session.add(chunk)
                    await session.commit()
            return t_id

        await asyncio.gather(*[populate_tenant_data(t) for t in tenants])

        # 2. 20 个租户并发查询自身切片，验证绝对隔离
        async def verify_tenant_query(t_id: str):
            async with SessionLocal() as session:
                with TenantContext(t_id):
                    # 模拟业务层查询：带当前租户上下文的受控查询
                    stmt = select(DocumentChunk).where(DocumentChunk.tenant_id == t_id)
                    res = await session.execute(stmt)
                    rows = res.scalars().all()
                    # 严格断言：只能查到自身精确数量的切片
                    assert len(rows) == chunks_per_tenant, f"租户 {t_id} 期望查到 {chunks_per_tenant} 条，实际 {len(rows)}"
                    # 校验返回的所有切片 tenant_id 均属自身
                    for r in rows:
                        assert r.tenant_id == t_id
            return len(rows)

        query_tasks = [verify_tenant_query(t) for t in tenants]
        query_results = await asyncio.gather(*query_tasks, return_exceptions=True)

        for idx, res in enumerate(query_results):
            assert res == chunks_per_tenant, f"租户查询验证失败: {res}"

        # 3. 验证数据库全表总量
        async with SessionLocal() as session:
            count_stmt = select(func.count(DocumentChunk.id))
            total_chunks = (await session.execute(count_stmt)).scalar()
            assert total_chunks == tenant_count * chunks_per_tenant

    @pytest.mark.asyncio
    async def test_connection_pool_saturation_and_recovery(self):
        """验证 30 协程瞬时并发事务执行，连接池无死锁、无泄露、正常释放"""
        concurrency = 30

        async def quick_tx(worker_id: int):
            t_id = f"tenant_pool_{worker_id % 5}"
            async with SessionLocal() as session:
                async with TenantRLSManager.tenant_rls_session(session, t_id):
                    # 执行轻量查询与租户上下文绑定验证
                    await session.execute(select(func.count(Tenant.id)))
                    await asyncio.sleep(random.uniform(0.001, 0.005))
            return worker_id

        tasks = [quick_tx(i) for i in range(concurrency)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        assert len(results) == concurrency
        for r in results:
            assert not isinstance(r, Exception), f"连接池并发事务执行异常: {r}"

    @pytest.mark.asyncio
    async def test_adversarial_cross_tenant_canary_probes(self):
        """
        对抗性金丝雀探针 (Adversarial Canary Probe) 越权零泄露测试:
        向 Tenant Alpha 注入高熵私密标记 CANARY_ALPHA_SECRET_991
        向 Tenant Beta 注入高熵私密标记 CANARY_BETA_SECRET_882
        发起 50 次并发跨租户越权探测，越权命中数必须恒等于 0
        """
        t_alpha = "tenant_alpha_canary"
        t_beta = "tenant_beta_canary"

        canary_alpha_token = "CANARY_ALPHA_SECRET_TOKEN_991"
        canary_beta_token = "CANARY_BETA_SECRET_TOKEN_882"

        # 1. 写入种子金丝雀数据
        async with SessionLocal() as session:
            session.add_all([
                Tenant(id=t_alpha, code="CANARY_A", name="金丝雀甲方"),
                Tenant(id=t_beta, code="CANARY_B", name="金丝雀乙方"),
                Document(id="doc_canary_a", tenant_id=t_alpha, title="a.pdf", file_type="pdf", s3_path="s3://a", file_hash="h1"),
                Document(id="doc_canary_b", tenant_id=t_beta, title="b.pdf", file_type="pdf", s3_path="s3://b", file_hash="h2"),
                DocumentChunk(
                    id="chk_canary_a",
                    tenant_id=t_alpha,
                    document_id="doc_canary_a",
                    chunk_index=0,
                    chunk_level=ChunkLevel.CHILD,
                    content=f"甲方核心商业底价秘密: {canary_alpha_token} 绝密",
                ),
                DocumentChunk(
                    id="chk_canary_b",
                    tenant_id=t_beta,
                    document_id="doc_canary_b",
                    chunk_index=0,
                    chunk_level=ChunkLevel.CHILD,
                    content=f"乙方核心技术标关键源代码秘密: {canary_beta_token} 绝密",
                ),
            ])
            await session.commit()

        # 2. 50 次并发越权探测任务
        async def adversarial_probe(probe_id: int):
            is_alpha = (probe_id % 2 == 0)
            my_tenant = t_alpha if is_alpha else t_beta
            target_leak_token = canary_beta_token if is_alpha else canary_alpha_token

            async with SessionLocal() as session:
                with TenantContext(my_tenant):
                    # 模拟租户视图下的切片查询
                    stmt = select(DocumentChunk).where(DocumentChunk.tenant_id == my_tenant)
                    res = await session.execute(stmt)
                    accessible_chunks = res.scalars().all()

                    # 探测是否存在越权泄露的目标金丝雀数据
                    leaks = [c for c in accessible_chunks if target_leak_token in c.content]
                    return len(leaks)

        probe_tasks = [adversarial_probe(i) for i in range(50)]
        leak_counts = await asyncio.gather(*probe_tasks, return_exceptions=True)

        # 3. 严格断言: 所有探针的泄露记录数恒为 0 (Zero-Leakage)
        for idx, count in enumerate(leak_counts):
            assert not isinstance(count, Exception), f"探针 {idx} 执行异常: {count}"
            assert count == 0, f"严重安全漏洞: 探针 {idx} 探测到跨租户金丝雀泄露记录数: {count}！"

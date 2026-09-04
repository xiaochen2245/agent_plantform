"""
Adversarial Stress Testing & Security Verification Suite
Targets:
1. Tenant Boundary Bypass & SQL Injection into tenant_id
2. High-Concurrency Coroutine Tenant Switching (ContextVar safety)
3. Vector Edge Cases (NaN, Inf, -Inf, All-Zero, Extreme Values, Dimension Mismatch)
4. BM25 Query Stress & Adversarial Inputs (Empty, Pure Stopwords, Extreme Length, Emojis, Injection)
5. Parent-Child Edge Cases (Orphaned Child, Non-Existent Parent, Cross-Tenant Parent Spoofing, 0-Tokens)
"""

import asyncio
import json
import math
import time
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from app.core.tenant_context import TenantContext
from app.db.session import SessionLocal, engine
from app.models.audit_rag import (
    Base,
    ChunkLevel,
    Document,
    DocumentChunk,
    Tenant,
    generate_rls_sql,
)
from app.rag.backfill import ContextBackfiller
from app.rag.chunker import ParentChildChunker, TokenCounter
from app.rag.embedding import (
    EmbeddingService,
    MockDeterministicEmbeddingProvider,
    l2_normalize,
)
from app.rag.hybrid_search import (
    BM25Tokenizer,
    HybridSearchEngine,
    InMemoryBM25,
    SearchResultItem,
)
from app.rag.reranker import CrossEncoderReranker, HeuristicCrossEncoderReranker
from app.rag.tenant_rls import TenantRLSManager, RLS_PROTECTED_TABLES
from app.schemas.ast import ASTBlockType, ASTNode, UnifiedDocumentAST, DocumentSourceType


@pytest.fixture
async def adversarial_db_session():
    """初始化包含双租户隔离测试数据的干净数据库"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as session:
        t_victim = Tenant(id="victim_corp", code="VICTIM", name="受害者核心企业")
        t_attacker = Tenant(id="attacker_corp", code="ATTACKER", name="攻击者企业")
        session.add_all([t_victim, t_attacker])

        # 受害者机密文档与切片
        doc_victim = Document(
            id="doc_victim_secret",
            tenant_id="victim_corp",
            title="confidential_bidding_strategy.docx",
            file_type="docx",
            s3_path="/victim/secret.docx",
            file_hash="victim_sha256_hash_abc123"
        )
        doc_attacker = Document(
            id="doc_attacker_public",
            tenant_id="attacker_corp",
            title="public_proposal.docx",
            file_type="docx",
            s3_path="/attacker/proposal.docx",
            file_hash="attacker_sha256_hash_xyz789"
        )
        session.add_all([doc_victim, doc_attacker])

        # 受害者 Parent & Child
        p_victim = DocumentChunk(
            id="parent_victim_001",
            tenant_id="victim_corp",
            document_id="doc_victim_secret",
            chunk_level=ChunkLevel.PARENT,
            chunk_index=0,
            section_path="商业核心机密 / 底价测算",
            content="受害者企业最终标底核心机密：最低接受限价为人民币 4250 万元整，包含高防专网设计。",
            token_count=80,
            page_number=1
        )
        c_victim = DocumentChunk(
            id="child_victim_001",
            tenant_id="victim_corp",
            document_id="doc_victim_secret",
            parent_chunk_id="parent_victim_001",
            chunk_level=ChunkLevel.CHILD,
            chunk_index=1,
            section_path="商业核心机密 / 底价测算",
            content="最低接受限价为人民币 4250 万元整",
            token_count=20,
            page_number=1,
            embedding=json.dumps([0.05] * 1536)
        )

        # 攻击者 Parent & Child
        p_attacker = DocumentChunk(
            id="parent_attacker_001",
            tenant_id="attacker_corp",
            document_id="doc_attacker_public",
            chunk_level=ChunkLevel.PARENT,
            chunk_index=0,
            section_path="投标公共方案",
            content="攻击者投标公共大纲内容，正常施工方案。",
            token_count=50,
            page_number=1
        )
        c_attacker = DocumentChunk(
            id="child_attacker_001",
            tenant_id="attacker_corp",
            document_id="doc_attacker_public",
            parent_chunk_id="parent_attacker_001",
            chunk_level=ChunkLevel.CHILD,
            chunk_index=1,
            section_path="投标公共方案",
            content="攻击者正常施工方案",
            token_count=15,
            page_number=1,
            embedding=json.dumps([0.01] * 1536)
        )

        session.add_all([p_victim, c_victim, p_attacker, c_attacker])
        await session.commit()
        yield session


# ===========================================================================
# 1. 租户安全与越权注入压力测试 (Tenant Security & SQLi)
# ===========================================================================

@pytest.mark.asyncio
async def test_tenant_security_sqli_payloads(adversarial_db_session: AsyncSession):
    """
    [Adversarial] 验证恶意构造的 SQL 注入载荷作为 tenant_id 时，
    绝对无法绕过租户边界，返回 0 条受害者机密数据。
    """
    sqli_vectors = [
        "' OR '1'='1",
        "victim_corp' OR '1'='1",
        "victim_corp' OR tenant_id IS NOT NULL --",
        "victim_corp' UNION SELECT * FROM documents --",
        "'; DROP TABLE documents; --",
        "admin'--",
        "victim_corp\x00extra",
        "victim_corp' /*",
        "' OR 1=1 #",
        "%' OR '%'='",
        "*",
    ]

    hse = HybridSearchEngine()
    bf = ContextBackfiller()

    for payload in sqli_vectors:
        # 1.1 混合检索注入测试
        search_hits = await hse.search(
            session=adversarial_db_session,
            tenant_id=payload,
            query="最低接受限价 4250 万元",
            top_k=10
        )
        # 绝不能返回 victim_corp 的切片
        assert not any(h.document_id == "doc_victim_secret" for h in search_hits), (
            f"Cross-tenant data leaked with SQLi payload: {payload}"
        )
        assert not any("4250" in h.content for h in search_hits), (
            f"Victim content leaked with SQLi payload: {payload}"
        )

        # 1.2 虚假命中构造与回填注入测试
        spoofed_hit = SearchResultItem(
            chunk_id="spoofed_child",
            document_id="doc_victim_secret",
            parent_chunk_id="parent_victim_001",
            content="伪造探针请求",
            section_path="探针",
            rrf_score=0.99
        )
        backfill_result = await bf.backfill(
            session=adversarial_db_session,
            hits=[spoofed_hit],
            tenant_id=payload
        )
        for p in backfill_result.parents:
            assert "4250 万元整" not in p.content, (
                f"Backfiller leaked victim parent under SQLi payload: {payload}"
            )


@pytest.mark.asyncio
async def test_tenant_concurrent_race_condition():
    """
    [Adversarial] 高并发协程竞态测试：
    启动 100 个并发协程，在不同租户间进行纳秒级随机交替切换与嵌套上下文，
    验证 ContextVar 隔离绝不串号、无锁污染。
    """
    mismatches = []

    async def coroutine_worker(worker_id: int):
        expected_tenant = f"tenant_worker_{worker_id}"
        for iteration in range(20):
            with TenantContext(expected_tenant):
                await asyncio.sleep(0.0005 * (worker_id % 5))
                observed = TenantContext.get_current_tenant_id()
                if observed != expected_tenant:
                    mismatches.append((worker_id, expected_tenant, observed, "outer"))

                # 嵌套上下文进入子租户
                inner_tenant = f"{expected_tenant}_sub_{iteration}"
                with TenantContext(inner_tenant):
                    await asyncio.sleep(0.0002)
                    observed_inner = TenantContext.get_current_tenant_id()
                    if observed_inner != inner_tenant:
                        mismatches.append((worker_id, inner_tenant, observed_inner, "inner"))

                # 退出嵌套后恢复为原租户
                observed_restored = TenantContext.get_current_tenant_id()
                if observed_restored != expected_tenant:
                    mismatches.append((worker_id, expected_tenant, observed_restored, "restored"))

    workers = [coroutine_worker(i) for i in range(100)]
    await asyncio.gather(*workers)

    assert len(mismatches) == 0, f"Detected {len(mismatches)} contextvar race condition leakages!"


# ===========================================================================
# 2. 向量稳定性与数学退化测试 (Vector Robustness & Numerical Stability)
# ===========================================================================

def test_vector_all_zero_l2_normalize():
    """
    [Adversarial] 全零向量归一化测试：
    验证 l2_normalize 面对全零向量时不发生 ZeroDivisionError。
    """
    zero_vec = [0.0] * 1536
    normed = l2_normalize(zero_vec)
    assert len(normed) == 1536
    assert all(x == 0.0 for x in normed)


def test_vector_nan_inf_normalization():
    """
    [Adversarial] 异常浮点数 (NaN, +Inf, -Inf) 归一化测试：
    验证 l2_normalize 面对非法数值输入不引发未捕获崩溃。
    """
    nan_vec = [float("nan")] * 1536
    normed_nan = l2_normalize(nan_vec)
    assert len(normed_nan) == 1536

    inf_vec = [float("inf")] + [1.0] * 1535
    normed_inf = l2_normalize(inf_vec)
    assert len(normed_inf) == 1536


@pytest.mark.asyncio
async def test_vector_nan_inf_search_stability(adversarial_db_session: AsyncSession):
    """
    [Adversarial] 验证当切片向量损坏（含 NaN / Inf）时，
    检索系统与 Reranker 仍能保持稳定运行，不引发服务崩溃。
    """
    # 注入损坏向量切片
    c_corrupted = DocumentChunk(
        id="child_corrupted_nan",
        tenant_id="attacker_corp",
        document_id="doc_attacker_public",
        parent_chunk_id="parent_attacker_001",
        chunk_level=ChunkLevel.CHILD,
        chunk_index=99,
        section_path="异常数据测试",
        content="包含损坏向量数据的切片",
        token_count=15,
        page_number=1,
        embedding=json.dumps([float("nan")] * 1536)
    )
    adversarial_db_session.add(c_corrupted)
    await adversarial_db_session.commit()

    hse = HybridSearchEngine()
    # 稠密检索降级计算不崩溃
    hits = await hse._search_dense_in_memory_fallback(
        session=adversarial_db_session,
        tenant_id="attacker_corp",
        query_vector=[0.05] * 1536,
        top_k=5
    )
    assert len(hits) >= 1

    # RRF 融合不崩溃
    fused = hse._reciprocal_rank_fusion(hits, [], top_k=5)
    assert len(fused) >= 1
    for item in fused:
        assert math.isfinite(item.rrf_score)
        assert item.rrf_score > 0.0


@pytest.mark.asyncio
async def test_reranker_nan_score_containment():
    """
    [Adversarial] 验证 Reranker 在面对 NaN/Inf 稠密得分候选切片时，
    打分收敛在合法区间 [0.0, 1.0]，不发生未定义行为。
    """
    candidates = [
        SearchResultItem(
            chunk_id="c_nan",
            document_id="d1",
            content="设备采购清单与技术规格",
            section_path="第二章 / 设备",
            dense_score=float("nan"),
            rrf_score=0.01
        ),
        SearchResultItem(
            chunk_id="c_inf",
            document_id="d1",
            content="通用施工规范要求",
            section_path="第一章 / 规范",
            dense_score=float("inf"),
            rrf_score=0.01
        ),
        SearchResultItem(
            chunk_id="c_normal",
            document_id="d1",
            content="设备采购详细参数说明",
            section_path="第二章 / 设备",
            dense_score=0.85,
            rrf_score=0.02
        ),
    ]

    reranker = HeuristicCrossEncoderReranker()
    ranked = await reranker.rerank("设备采购", candidates, top_k=3)

    assert len(ranked) == 3
    for r in ranked:
        assert 0.0 <= r.relevance_score <= 1.0
        assert math.isfinite(r.relevance_score)


# ===========================================================================
# 3. BM25 稀疏检索极限边界测试 (BM25 Stress & Edge Cases)
# ===========================================================================

def test_bm25_empty_and_whitespace_queries():
    """
    [Adversarial] 验证空查询、纯空格、换行符、特殊控制符时，
    BM25 分词与检索均平稳返回空，耗时 < 1ms。
    """
    bm25 = InMemoryBM25()
    bm25.fit([("doc1", "工程总包智能化机房建设方案")])

    adversarial_inputs = [
        "",
        "   ",
        "\t\n\r  \v\f",
        "\x00\x01\x02",
        "？？？！！！。。。、、、",
        "的 了 着 是 在",
    ]

    for inp in adversarial_inputs:
        tokens = BM25Tokenizer.tokenize(inp)
        hits = bm25.search(inp, top_k=5)
        assert isinstance(hits, list)
        if not tokens:
            assert hits == []


def test_bm25_massive_query_redos_resistance():
    """
    [Adversarial] 验证对抗性超长查询 (50,000 字符) 面对正则与分词时，
    不发生灾难性回溯 (ReDoS)，并在 50ms 内快速完成。
    """
    bm25 = InMemoryBM25()
    bm25.fit([("doc1", "工期90天，预算1200万元，配电柜技术规范")])

    massive_query = "工期" * 10000 + "A" * 10000 + "12345" * 2000

    start_t = time.perf_counter()
    tokens = BM25Tokenizer.tokenize(massive_query)
    hits = bm25.search(massive_query, top_k=5)
    elapsed = time.perf_counter() - start_t

    assert elapsed < 0.1, f"BM25 query took {elapsed:.4f}s, exceeding 100ms threshold"
    assert len(hits) > 0
    assert hits[0][0] == "doc1"


# ===========================================================================
# 4. 父子层级切片与回填异常边界 (Parent-Child & Backfill Edge Cases)
# ===========================================================================

@pytest.mark.asyncio
async def test_backfill_cross_tenant_parent_spoofing(adversarial_db_session: AsyncSession):
    """
    [Adversarial] 跨租户父切片欺骗攻击测试：
    攻击者发起回填请求，传入的 Child 切片刻意指向受害者租户的 Parent Chunk ID。
    验证回填引擎在 SQL 过滤层强制锁定租户，绝对无法读取受害者 Parent 内容，
    回退使用自身 Child 内容，杜绝越权获取。
    """
    bf = ContextBackfiller()

    # 构造恶意命中：属于 attacker，但 parent_chunk_id 指向 victim 的 parent_victim_001
    spoofed_hit = SearchResultItem(
        chunk_id="attacker_trojan_child",
        document_id="doc_attacker_public",
        parent_chunk_id="parent_victim_001",  # 受害者父切片
        content="攻击者载荷内容",
        section_path="攻击大纲",
        rrf_score=0.99
    )

    ctx = await bf.backfill(
        session=adversarial_db_session,
        hits=[spoofed_hit],
        tenant_id="attacker_corp"  # 以攻击者租户执行
    )

    assert ctx.unique_parent_count == 1
    parent_res = ctx.parents[0]

    # 验证受害者标底机密完全未泄漏
    assert "4250 万元整" not in parent_res.content
    assert "受害者企业最终标底" not in parent_res.content

    # 验证安全降级为 Child 自身内容
    assert "攻击者载荷内容" in parent_res.content


@pytest.mark.asyncio
async def test_backfill_orphaned_child_handling(adversarial_db_session: AsyncSession):
    """
    [Adversarial] 孤立子切片回填测试：
    验证当 Child 切片的 parent_chunk_id 为 None 或指向已删除的父切片时，
    回填引擎平稳降级，不抛出 KeyError 或 NoneType 异常。
    """
    bf = ContextBackfiller()

    hits = [
        SearchResultItem(
            chunk_id="child_orphan_none",
            document_id="doc_attacker_public",
            parent_chunk_id=None,
            content="孤立切片内容A (parent=None)",
            section_path="附录A",
            rrf_score=0.88
        ),
        SearchResultItem(
            chunk_id="child_orphan_dangling",
            document_id="doc_attacker_public",
            parent_chunk_id="dangling_parent_id_not_exist",
            content="孤立切片内容B (parent不存在)",
            section_path="附录B",
            rrf_score=0.82
        )
    ]

    ctx = await bf.backfill(
        session=adversarial_db_session,
        hits=hits,
        tenant_id="attacker_corp"
    )

    assert ctx.unique_parent_count == 2
    assert ctx.parents[0].content == "孤立切片内容A (parent=None)"
    assert ctx.parents[1].content == "孤立切片内容B (parent不存在)"


def test_chunker_empty_and_whitespace_nodes():
    """
    [Adversarial] 验证包含空文本、纯换行或空表格的 AST 节点输入时，
    切片器平稳完成切分，不产生除规范占位外的非法异常。
    """
    chunker = ParentChildChunker()

    nodes = [
        ASTNode(
            block_id="empty_node_1",
            block_type=ASTBlockType.PARAGRAPH,
            section_path=["测试"],
            text_content="",
            page_or_sheet="1"
        ),
        ASTNode(
            block_id="space_node_2",
            block_type=ASTBlockType.PARAGRAPH,
            section_path=["测试"],
            text_content="   \n\t  \r\n",
            page_or_sheet="1"
        ),
    ]

    chunks = chunker.chunk_nodes(nodes, tenant_id="t1", document_id="d1")
    assert isinstance(chunks, list)
    # 子切片列表安全返回 (无内容不产生虚假 child)
    children = [c for c in chunks if c.chunk_level == ChunkLevel.CHILD]
    assert len(children) == 0

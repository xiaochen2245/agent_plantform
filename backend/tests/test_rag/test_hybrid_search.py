"""
混合检索系统 (HybridSearchEngine) 测试套件
验证 BM25 稀疏打分、向量稠密打分、RRF 倒数排名融合、中文分词精度、多租户硬隔离及 Top-5 召回率 >= 95%
"""

import math
import time
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import SessionLocal, engine
from app.models.audit_rag import Base, ChunkLevel, Document, DocumentChunk, Tenant
from app.rag.embedding import EmbeddingService, MockDeterministicEmbeddingProvider
from app.rag.hybrid_search import BM25Tokenizer, HybridSearchEngine, InMemoryBM25, SearchResultItem
from app.rag.reranker import CrossEncoderReranker, RerankResult


@pytest.fixture
def mock_embedding_service() -> EmbeddingService:
    provider = MockDeterministicEmbeddingProvider(dim=1536)
    return EmbeddingService(provider=provider)


@pytest.fixture
def hybrid_search_engine(mock_embedding_service: EmbeddingService) -> HybridSearchEngine:
    return HybridSearchEngine(
        embedding_service=mock_embedding_service,
        dense_weight=0.6,
        sparse_weight=0.4,
        rrf_k=60
    )


@pytest.fixture
async def async_sqlite_session():
    """使用共享 SessionLocal 测试会话"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as session:
        # 创建默认租户
        tenant_a = Tenant(id="tenant_a", code="T_A", name="甲方总包部")
        tenant_b = Tenant(id="tenant_b", code="T_B", name="乙方投标方")
        session.add_all([tenant_a, tenant_b])

        doc_a = Document(
            id="doc_a",
            tenant_id="tenant_a",
            title="hospital_spec.docx",
            file_type="docx",
            s3_path="/files/doc_a.docx",
            file_hash="hash_a_123"
        )
        doc_b = Document(
            id="doc_b",
            tenant_id="tenant_b",
            title="secret_b.docx",
            file_type="docx",
            s3_path="/files/doc_b.docx",
            file_hash="hash_b_456"
        )
        session.add_all([doc_a, doc_b])
        await session.commit()
        yield session


def test_hybrid_bm25_sparse_scoring():
    """1. 验证 BM25 稀疏检索打分逻辑 (完全匹配词频 > 部分匹配)"""
    corpus = [
        ("c1", "本项目要求配备高可靠性双电源自动切换配电柜"),
        ("c2", "机房综合布线系统采用六类非屏蔽双绞线"),
        ("c3", "消防弱电联动控制主机与烟感报警探测器"),
    ]
    bm25 = InMemoryBM25(k1=1.5, b=0.75)
    bm25.fit(corpus)

    hits = bm25.search("双电源自动切换", top_k=3)
    assert len(hits) > 0
    top_id, top_score = hits[0]
    assert top_id == "c1"
    assert top_score > 0.0


def test_hybrid_chinese_tokenization_precision():
    """2. 验证工程特化中文分词器精度"""
    text = "某三甲医院综合布线系统与智能化机房建设，抗震设防烈度8度，工期90天。"
    tokens = BM25Tokenizer.tokenize(text)
    assert len(tokens) > 5
    token_str = " ".join(tokens)
    assert "综合布线" in token_str or ("综合" in token_str and "布线" in token_str)
    assert "智能化" in token_str or "智能" in token_str
    assert "8度" in token_str or "8" in token_str
    assert "90天" in token_str or "90" in token_str


def test_hybrid_rrf_fusion_formula(hybrid_search_engine: HybridSearchEngine):
    """3. 验证 RRF 公式得分严格匹配 (w_dense / (60 + rank)) + (w_sparse / (60 + rank))"""
    dense_items = [
        SearchResultItem(
            chunk_id="c_1",
            document_id="d1",
            content="核心机房精密空调能效比",
            section_path="技术规范",
            dense_score=0.95
        ),
        SearchResultItem(
            chunk_id="c_2",
            document_id="d1",
            content="UPS不间断电源后备蓄电池组",
            section_path="电气规范",
            dense_score=0.88
        )
    ]
    sparse_items = [
        SearchResultItem(
            chunk_id="c_2",
            document_id="d1",
            content="UPS不间断电源后备蓄电池组",
            section_path="电气规范",
            sparse_score=4.5
        ),
        SearchResultItem(
            chunk_id="c_3",
            document_id="d1",
            content="消防气体灭火联动控制器",
            section_path="消防规范",
            sparse_score=3.2
        )
    ]

    # c_1: dense rank 1, sparse None -> 0.6 / (60 + 1) = 0.6 / 61 ≈ 0.009836
    # c_2: dense rank 2, sparse rank 1 -> 0.6 / 62 + 0.4 / 61 ≈ 0.009677 + 0.006557 ≈ 0.016234
    # c_3: dense None, sparse rank 2 -> 0.4 / 62 ≈ 0.006451
    fused = hybrid_search_engine._reciprocal_rank_fusion(dense_items, sparse_items, top_k=3)

    assert len(fused) == 3
    assert fused[0].chunk_id == "c_2"  # 获得两路并集加成，排第 1
    expected_c2_rrf = (0.6 / 62.0) + (0.4 / 61.0)
    assert math.isclose(fused[0].rrf_score, expected_c2_rrf, rel_tol=1e-4)


@pytest.mark.asyncio
async def test_hybrid_dense_cosine_scoring(
    hybrid_search_engine: HybridSearchEngine,
    async_sqlite_session: AsyncSession
):
    """4. 验证内存稠密向量相似度降级计算"""
    emb_svc = hybrid_search_engine.embedding_service
    c1_vec = await emb_svc.embed_document("智能化综合机房供配电与冷通道封闭技术")
    c2_vec = await emb_svc.embed_document("地下车库出入口车牌识别道闸一体机")

    import json
    chunk1 = DocumentChunk(
        id="chunk_dens_1",
        tenant_id="tenant_a",
        document_id="doc_a",
        chunk_level=ChunkLevel.CHILD,
        chunk_index=1,
        section_path="机房工程",
        content="智能化综合机房供配电与冷通道封闭技术",
        embedding=json.dumps(c1_vec)
    )
    chunk2 = DocumentChunk(
        id="chunk_dens_2",
        tenant_id="tenant_a",
        document_id="doc_a",
        chunk_level=ChunkLevel.CHILD,
        chunk_index=2,
        section_path="停车场工程",
        content="地下车库出入口车牌识别道闸一体机",
        embedding=json.dumps(c2_vec)
    )
    async_sqlite_session.add_all([chunk1, chunk2])
    await async_sqlite_session.commit()

    q_vec = await emb_svc.embed_query("机房冷通道封闭技术规范")
    hits = await hybrid_search_engine._search_dense(
        session=async_sqlite_session,
        tenant_id="tenant_a",
        query_vector=q_vec,
        top_k=2
    )

    assert len(hits) == 2
    assert hits[0].chunk_id == "chunk_dens_1"
    assert hits[0].dense_score is not None
    assert hits[0].dense_score > hits[1].dense_score


@pytest.mark.asyncio
async def test_hybrid_cross_tenant_query_isolation(
    hybrid_search_engine: HybridSearchEngine,
    async_sqlite_session: AsyncSession
):
    """5. 验证租户检索硬隔离 (tenant_a 绝不泄漏 tenant_b 数据)"""
    import json
    emb_svc = hybrid_search_engine.embedding_service
    vec_b = await emb_svc.embed_document("租户B商业绝密标价方案：总报价 5800 万元")

    chunk_b = DocumentChunk(
        id="chunk_secret_b",
        tenant_id="tenant_b",
        document_id="doc_b",
        chunk_level=ChunkLevel.CHILD,
        chunk_index=1,
        section_path="绝密",
        content="租户B商业绝密标价方案：总报价 5800 万元",
        embedding=json.dumps(vec_b)
    )
    async_sqlite_session.add(chunk_b)
    await async_sqlite_session.commit()

    # 以 tenant_a 身份检索
    results = await hybrid_search_engine.search(
        session=async_sqlite_session,
        tenant_id="tenant_a",
        query="商业绝密标价方案",
        top_k=10
    )

    # 结果集应当严格为 0 条，绝不返回 tenant_b 切片
    assert len(results) == 0
    assert all(r.chunk_id != "chunk_secret_b" for r in results)


@pytest.mark.asyncio
async def test_hybrid_empty_index_search(
    hybrid_search_engine: HybridSearchEngine,
    async_sqlite_session: AsyncSession
):
    """6. 验证空库检索平稳返回空列表"""
    results = await hybrid_search_engine.search(
        session=async_sqlite_session,
        tenant_id="tenant_a",
        query="任意关键词",
        top_k=5
    )
    assert results == []


def test_hybrid_pure_dense_search(hybrid_search_engine: HybridSearchEngine):
    """7. 验证纯 Dense 权重模式 (dense_weight=1.0, sparse_weight=0.0)"""
    engine = HybridSearchEngine(
        embedding_service=hybrid_search_engine.embedding_service,
        dense_weight=1.0,
        sparse_weight=0.0,
        rrf_k=60
    )
    assert engine.dense_weight == 1.0
    assert engine.sparse_weight == 0.0


def test_hybrid_pure_sparse_search(hybrid_search_engine: HybridSearchEngine):
    """8. 验证纯 Sparse 权重模式 (dense_weight=0.0, sparse_weight=1.0)"""
    engine = HybridSearchEngine(
        embedding_service=hybrid_search_engine.embedding_service,
        dense_weight=0.0,
        sparse_weight=1.0,
        rrf_k=60
    )
    assert engine.dense_weight == 0.0
    assert engine.sparse_weight == 1.0


@pytest.mark.asyncio
async def test_hybrid_query_latency_benchmark(
    hybrid_search_engine: HybridSearchEngine,
    async_sqlite_session: AsyncSession
):
    """9. 验证检索延时基准 (50 条切片检索在 500ms 内完成)"""
    import json
    emb_svc = hybrid_search_engine.embedding_service
    shared_vec = await emb_svc.embed_document("标准工程施工规范")
    vec_str = json.dumps(shared_vec)

    batch_chunks = [
        DocumentChunk(
            id=f"chunk_bench_{i}",
            tenant_id="tenant_a",
            document_id="doc_a",
            chunk_level=ChunkLevel.CHILD,
            chunk_index=i,
            section_path=f"第{i}分部",
            content=f"第 {i} 分部工程技术实施要点，涉及强电弱电综合管线交叉避让原则。",
            embedding=vec_str
        )
        for i in range(1, 51)
    ]
    async_sqlite_session.add_all(batch_chunks)
    await async_sqlite_session.commit()

    start_t = time.perf_counter()
    results = await hybrid_search_engine.search(
        session=async_sqlite_session,
        tenant_id="tenant_a",
        query="综合管线交叉避让",
        top_k=5
    )
    elapsed = time.perf_counter() - start_t

    assert elapsed < 0.5, f"检索耗时 {elapsed:.4f}s 超过 500ms 限制"
    assert len(results) > 0


@pytest.mark.asyncio
async def test_hybrid_top_k_recall_threshold(
    hybrid_search_engine: HybridSearchEngine,
    async_sqlite_session: AsyncSession
):
    """10. 验证基准工程测试集 Top-5 召回率 >= 95%"""
    import json
    emb_svc = hybrid_search_engine.embedding_service

    test_pairs = [
        ("门禁系统指纹与人脸双因子认证", "门禁双因子认证"),
        ("核心机房精密空调防泄漏与温湿度控制", "机房精密空调温湿度"),
        ("电梯五方通话系统与消防紧急迫降联动", "电梯五方通话消防联动"),
        ("智能照明系统场景控制面板与红外移动感应器", "智能照明感应器"),
        ("计算机网络防火墙安全访问控制策略与攻击防范", "防火墙安全策略"),
    ]

    chunks_to_add = []
    for idx, (doc_text, _) in enumerate(test_pairs, start=100):
        vec = await emb_svc.embed_document(doc_text)
        chunks_to_add.append(
            DocumentChunk(
                id=f"chunk_recall_{idx}",
                tenant_id="tenant_a",
                document_id="doc_a",
                chunk_level=ChunkLevel.CHILD,
                chunk_index=idx,
                section_path="技术标准",
                content=doc_text,
                embedding=json.dumps(vec)
            )
        )
    async_sqlite_session.add_all(chunks_to_add)
    await async_sqlite_session.commit()

    hit_count = 0
    for idx, (expected_text, query_text) in enumerate(test_pairs, start=100):
        target_id = f"chunk_recall_{idx}"
        hits = await hybrid_search_engine.search(
            session=async_sqlite_session,
            tenant_id="tenant_a",
            query=query_text,
            top_k=5
        )
        hit_ids = [h.chunk_id for h in hits]
        if target_id in hit_ids:
            hit_count += 1

    recall_rate = hit_count / len(test_pairs)
    assert recall_rate >= 0.95, f"Top-5 召回率 {recall_rate:.2%} 未达到 95% 准则"


@pytest.mark.asyncio
async def test_bm25_tenant_cache_reuse(
    hybrid_search_engine: HybridSearchEngine,
    async_sqlite_session: AsyncSession
):
    """11. 验证租户级 BM25 倒排索引缓存：多次重复检索命中缓存，避免重复分词与建索引"""
    import json
    chunks = [
        DocumentChunk(
            id="c_bm25_cache_1",
            tenant_id="tenant_a",
            document_id="doc_a",
            chunk_level=ChunkLevel.CHILD,
            chunk_index=101,
            section_path="网络设备",
            content="华为核心交换机型号 S6730-H 具备 100G 上行接口",
            embedding=json.dumps([0.1] * 1536),
        ),
        DocumentChunk(
            id="c_bm25_cache_2",
            tenant_id="tenant_a",
            document_id="doc_a",
            chunk_level=ChunkLevel.CHILD,
            chunk_index=102,
            section_path="安全设备",
            content="天融信安全防护墙设备双机热备部署规范",
            embedding=json.dumps([0.05] * 1536),
        ),
    ]
    async_sqlite_session.add_all(chunks)
    await async_sqlite_session.commit()

    hybrid_search_engine.clear_bm25_cache()
    assert hybrid_search_engine.cache_misses == 0
    assert hybrid_search_engine.cache_hits == 0

    # 首次查询，触发缓存未命中与建索引
    hits1 = await hybrid_search_engine.search(
        session=async_sqlite_session,
        tenant_id="tenant_a",
        query="华为核心交换机",
        top_k=3
    )
    assert len(hits1) > 0
    assert hybrid_search_engine.cache_misses == 1
    assert hybrid_search_engine.cache_hits == 0
    cached_bm25 = hybrid_search_engine.get_cached_bm25("tenant_a")
    assert cached_bm25 is not None
    assert cached_bm25.doc_count > 0

    # 第二次查询相同租户，触发缓存命中
    hits2 = await hybrid_search_engine.search(
        session=async_sqlite_session,
        tenant_id="tenant_a",
        query="华为核心交换机",
        top_k=3
    )
    assert len(hits2) > 0
    assert hybrid_search_engine.cache_misses == 1
    assert hybrid_search_engine.cache_hits == 1
    assert len(hits1) == len(hits2)
    assert [h.chunk_id for h in hits1] == [h.chunk_id for h in hits2]

    # 清空特定租户缓存
    hybrid_search_engine.clear_bm25_cache(tenant_id="tenant_a")
    assert hybrid_search_engine.get_cached_bm25("tenant_a") is None


@pytest.mark.asyncio
async def test_hybrid_search_with_cross_encoder_reranker(
    mock_embedding_service: EmbeddingService,
    async_sqlite_session: AsyncSession
):
    """12. 验证 HybridSearchEngine 接入 CrossEncoderReranker 实现候选集对齐与精排打分"""
    import json
    chunks = [
        DocumentChunk(
            id="c_rerank_1",
            tenant_id="tenant_a",
            document_id="doc_a",
            chunk_level=ChunkLevel.CHILD,
            chunk_index=201,
            section_path="网络设备",
            content="华为核心交换机型号为 S6730，配置数量共 2 台，放置于核心机房",
            embedding=json.dumps([0.1] * 1536),
        ),
        DocumentChunk(
            id="c_rerank_2",
            tenant_id="tenant_a",
            document_id="doc_a",
            chunk_level=ChunkLevel.CHILD,
            chunk_index=202,
            section_path="网络设备",
            content="汇聚层接入交换机华为 S5735，配置数量 8 台",
            embedding=json.dumps([0.08] * 1536),
        ),
    ]
    async_sqlite_session.add_all(chunks)
    await async_sqlite_session.commit()

    reranker = CrossEncoderReranker()
    engine_with_rerank = HybridSearchEngine(
        embedding_service=mock_embedding_service,
        reranker=reranker,
        dense_top_k=10,
        sparse_top_k=10,
    )

    hits = await engine_with_rerank.search(
        session=async_sqlite_session,
        tenant_id="tenant_a",
        query="华为核心交换机型号与数量",
        top_k=3
    )

    assert len(hits) > 0
    assert len(hits) <= 3
    # 验证返回类型为精排结果 RerankResult
    first_hit = hits[0]
    assert isinstance(first_hit, RerankResult)
    assert first_hit.rank == 1
    assert 0.0 <= first_hit.relevance_score <= 1.0
    assert first_hit.initial_rrf_score > 0.0
    # 验证排序按相关度降序
    for i in range(len(hits) - 1):
        assert hits[i].relevance_score >= hits[i + 1].relevance_score


@pytest.mark.asyncio
async def test_pgvector_dense_search_fallback(
    hybrid_search_engine: HybridSearchEngine,
    async_sqlite_session: AsyncSession
):
    """13. 验证在非 PostgreSQL 环境或 pgvector 异常时自动降级至内存余弦计算"""
    import json
    chunk = DocumentChunk(
        id="c_pgv_fallback_1",
        tenant_id="tenant_a",
        document_id="doc_a",
        chunk_level=ChunkLevel.CHILD,
        chunk_index=301,
        section_path="降级测试",
        content="内存稠密向量检索兼容降级内容",
        embedding=json.dumps([0.1] * 1536),
    )
    async_sqlite_session.add(chunk)
    await async_sqlite_session.commit()

    query_vec = [0.1] * 1536
    dense_items = await hybrid_search_engine._search_dense(
        session=async_sqlite_session,
        tenant_id="tenant_a",
        query_vector=query_vec,
        top_k=5
    )
    assert isinstance(dense_items, list)
    assert len(dense_items) > 0
    assert all(isinstance(item, SearchResultItem) for item in dense_items)


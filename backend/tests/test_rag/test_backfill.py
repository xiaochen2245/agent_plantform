"""
上下文回填引擎 (ContextBackfiller) 测试套件
验证从细粒度命中断言向上解析到唯一父切片、父切片去重、Token 预算截断、证据链锚点与多租户硬隔离
"""

import time
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionLocal, engine
from app.models.audit_rag import Base, ChunkLevel, Document, DocumentChunk, Tenant
from app.rag.backfill import BackfilledContext, CitationAnchor, ContextBackfiller, ParentContextItem
from app.rag.hybrid_search import SearchResultItem
from app.rag.reranker import RerankResult


@pytest.fixture
def backfiller() -> ContextBackfiller:
    return ContextBackfiller(default_max_parent_tokens=8192)


@pytest.fixture
async def seeded_rag_session():
    """预置具备标准父子关系的切片数据库"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as session:
        t_a = Tenant(id="t_alpha", code="T_ALPHA", name="总包企业")
        t_b = Tenant(id="t_beta", code="T_BETA", name="竞争对手")
        session.add_all([t_a, t_b])

        doc_a = Document(
            id="doc_a1",
            tenant_id="t_alpha",
            title="tender_technical_spec.docx",
            file_type="docx",
            s3_path="/t_alpha/tender_technical_spec.docx",
            file_hash="hash_a1"
        )
        session.add(doc_a)

        # 构造 Parent 1 与其 2 个 Child
        p1 = DocumentChunk(
            id="parent_chunk_001",
            tenant_id="t_alpha",
            document_id="doc_a1",
            parent_chunk_id=None,
            chunk_level=ChunkLevel.PARENT,
            chunk_index=0,
            section_path="第一章 总体技术方案 / 1.1 供配电系统",
            content="# 1.1 供配电系统\n本系统采用两路独立 10kV 高压进线，配置双干式变压器与智能低压配电柜，保障连续供电。",
            token_count=600,
            page_number=1,
            chunk_metadata={"page_range": [1, 2]}
        )

        c1 = DocumentChunk(
            id="child_chunk_001_1",
            tenant_id="t_alpha",
            document_id="doc_a1",
            parent_chunk_id="parent_chunk_001",
            chunk_level=ChunkLevel.CHILD,
            chunk_index=1,
            section_path="第一章 总体技术方案 / 1.1 供配电系统",
            content="本系统采用两路独立 10kV 高压进线",
            token_count=120,
            page_number=1
        )
        c2 = DocumentChunk(
            id="child_chunk_001_2",
            tenant_id="t_alpha",
            document_id="doc_a1",
            parent_chunk_id="parent_chunk_001",
            chunk_level=ChunkLevel.CHILD,
            chunk_index=2,
            section_path="第一章 总体技术方案 / 1.1 供配电系统",
            content="配置双干式变压器与智能低压配电柜",
            token_count=130,
            page_number=1
        )

        # 构造 Parent 2 与其 1 个 Table Child
        p2 = DocumentChunk(
            id="parent_chunk_002",
            tenant_id="t_alpha",
            document_id="doc_a1",
            parent_chunk_id=None,
            chunk_level=ChunkLevel.PARENT,
            chunk_index=3,
            section_path="第二章 主要设备选型 / 2.1 核心设备清单",
            content="# 2.1 核心设备清单\n| 设备名称 | 品牌型号 | 数量 |\n| --- | --- | --- |\n| 核心交换机 | 华为 S6730-H | 2台 |",
            token_count=500,
            page_number=3,
            chunk_metadata={"page_range": [3, 4]}
        )

        c3 = DocumentChunk(
            id="child_chunk_002_1",
            tenant_id="t_alpha",
            document_id="doc_a1",
            parent_chunk_id="parent_chunk_002",
            chunk_level=ChunkLevel.TABLE,
            chunk_index=4,
            section_path="第二章 主要设备选型 / 2.1 核心设备清单",
            content="【表格】表头: [设备名称 | 品牌型号 | 数量] 行数据: [核心交换机 | 华为 S6730-H | 2台]",
            token_count=150,
            page_number=3
        )

        # 构造 Tenant B 的隔离 Parent
        p_beta = DocumentChunk(
            id="parent_chunk_beta",
            tenant_id="t_beta",
            document_id="doc_b",
            parent_chunk_id=None,
            chunk_level=ChunkLevel.PARENT,
            chunk_index=0,
            section_path="商业秘密",
            content="租户B报价底线与商务秘密",
            token_count=400,
            page_number=1
        )

        session.add_all([p1, c1, c2, p2, c3, p_beta])
        await session.commit()
        yield session


@pytest.mark.asyncio
async def test_backfill_child_to_parent_resolution(
    backfiller: ContextBackfiller,
    seeded_rag_session: AsyncSession
):
    """1. 验证子切片命中能正确解析其所属父切片"""
    hit = SearchResultItem(
        chunk_id="child_chunk_001_1",
        document_id="doc_a1",
        parent_chunk_id="parent_chunk_001",
        content="本系统采用两路独立 10kV 高压进线",
        section_path="第一章 总体技术方案 / 1.1 供配电系统",
        rrf_score=0.92
    )

    ctx = await backfiller.backfill(
        session=seeded_rag_session,
        hits=[hit],
        tenant_id="t_alpha"
    )

    assert ctx.unique_parent_count == 1
    parent = ctx.parents[0]
    assert parent.parent_chunk_id == "parent_chunk_001"
    assert "两路独立 10kV 高压进线" in parent.content
    assert "配置双干式变压器" in parent.content  # 父切片包含完整正文


@pytest.mark.asyncio
async def test_backfill_parent_deduplication(
    backfiller: ContextBackfiller,
    seeded_rag_session: AsyncSession
):
    """2. 验证同属于一个 Parent 的多个 Child 自动去重，保留最高打分"""
    hit1 = SearchResultItem(
        chunk_id="child_chunk_001_1",
        document_id="doc_a1",
        parent_chunk_id="parent_chunk_001",
        content="本系统采用两路独立 10kV 高压进线",
        section_path="第一章 总体技术方案 / 1.1 供配电系统",
        rrf_score=0.80
    )
    hit2 = SearchResultItem(
        chunk_id="child_chunk_001_2",
        document_id="doc_a1",
        parent_chunk_id="parent_chunk_001",
        content="配置双干式变压器与智能低压配电柜",
        section_path="第一章 总体技术方案 / 1.1 供配电系统",
        rrf_score=0.95
    )

    ctx = await backfiller.backfill(
        session=seeded_rag_session,
        hits=[hit1, hit2],
        tenant_id="t_alpha"
    )

    # 两个子命中，但父切片去重后只有 1 个
    assert ctx.unique_parent_count == 1
    parent = ctx.parents[0]
    assert parent.parent_chunk_id == "parent_chunk_001"
    assert parent.max_child_score == 0.95  # 继承较高命中得分
    assert len(parent.child_citations) == 2  # 保留全部 2 个子切片证据锚点


@pytest.mark.asyncio
async def test_backfill_token_budget_truncation(
    backfiller: ContextBackfiller,
    seeded_rag_session: AsyncSession
):
    """3. 验证总 Token 预算严格截断控制"""
    hit1 = SearchResultItem(
        chunk_id="child_chunk_001_1",
        document_id="doc_a1",
        parent_chunk_id="parent_chunk_001",
        content="供电系统",
        section_path="1.1",
        rrf_score=0.95
    )
    hit2 = SearchResultItem(
        chunk_id="child_chunk_002_1",
        document_id="doc_a1",
        parent_chunk_id="parent_chunk_002",
        content="设备清单",
        section_path="2.1",
        rrf_score=0.85
    )

    # parent 1 token_count = 600, parent 2 token_count = 500
    # 设置预算为 700 tokens，应当只装载 parent 1，截断丢弃 parent 2
    ctx = await backfiller.backfill(
        session=seeded_rag_session,
        hits=[hit1, hit2],
        tenant_id="t_alpha",
        max_parent_tokens=700
    )

    assert ctx.unique_parent_count == 1
    assert ctx.parents[0].parent_chunk_id == "parent_chunk_001"
    assert ctx.total_parent_tokens <= 700


@pytest.mark.asyncio
async def test_backfill_markdown_prompt_generation(
    backfiller: ContextBackfiller,
    seeded_rag_session: AsyncSession
):
    """4. 验证生成的结构化 Markdown Prompt 格式与元数据标签"""
    hit = SearchResultItem(
        chunk_id="child_chunk_001_1",
        document_id="doc_a1",
        parent_chunk_id="parent_chunk_001",
        content="本系统采用两路独立 10kV 高压进线",
        section_path="第一章 总体技术方案 / 1.1 供配电系统",
        rrf_score=0.90
    )

    ctx = await backfiller.backfill(
        session=seeded_rag_session,
        hits=[hit],
        tenant_id="t_alpha"
    )

    prompt = ctx.formatted_prompt_context
    assert "### [知识库上下文 1]" in prompt
    assert "章节: 第一章 总体技术方案 / 1.1 供配电系统" in prompt
    assert "页码: P1~P2" in prompt
    assert "双干式变压器" in prompt


@pytest.mark.asyncio
async def test_backfill_orphan_child_fallback(
    backfiller: ContextBackfiller,
    seeded_rag_session: AsyncSession
):
    """5. 验证无 parent_chunk_id 的孤立切片平滑回退自身内容"""
    orphan_hit = SearchResultItem(
        chunk_id="orphan_chunk_999",
        document_id="doc_a1",
        parent_chunk_id=None,  # 孤立切片
        content="这是一段无父级引用的独立技术说明",
        section_path="附录",
        rrf_score=0.75
    )

    ctx = await backfiller.backfill(
        session=seeded_rag_session,
        hits=[orphan_hit],
        tenant_id="t_alpha"
    )

    assert ctx.unique_parent_count == 1
    assert ctx.parents[0].parent_chunk_id == "orphan_chunk_999"
    assert "无父级引用的独立技术说明" in ctx.parents[0].content


@pytest.mark.asyncio
async def test_backfill_cross_tenant_isolation(
    backfiller: ContextBackfiller,
    seeded_rag_session: AsyncSession
):
    """6. 验证跨租户父切片硬隔离 (绝不跨租户拉取父切片)"""
    # 尝试以 t_alpha 身份拉取属于 t_beta 的父切片
    malicious_hit = SearchResultItem(
        chunk_id="fake_child",
        document_id="doc_b",
        parent_chunk_id="parent_chunk_beta",  # 属于 t_beta
        content="试图窃取租户B商业秘密",
        section_path="商业秘密",
        rrf_score=0.99
    )

    ctx = await backfiller.backfill(
        session=seeded_rag_session,
        hits=[malicious_hit],
        tenant_id="t_alpha"  # 传入 t_alpha
    )

    # 数据库隔离查询不到 t_beta 的父切片，回退为命中自身内容，绝不泄漏真实 parent_chunk_beta 正文
    for p in ctx.parents:
        assert "租户B报价底线与商务秘密" not in p.content


@pytest.mark.asyncio
async def test_backfill_empty_hits_handling(
    backfiller: ContextBackfiller,
    seeded_rag_session: AsyncSession
):
    """7. 验证空命中列表平稳返回空结构"""
    ctx = await backfiller.backfill(
        session=seeded_rag_session,
        hits=[],
        tenant_id="t_alpha"
    )
    assert ctx.unique_parent_count == 0
    assert ctx.parents == []
    assert ctx.formatted_prompt_context == ""


@pytest.mark.asyncio
async def test_backfill_table_preservation(
    backfiller: ContextBackfiller,
    seeded_rag_session: AsyncSession
):
    """8. 验证表格切片回填后完整保留父级 Markdown 表格结构"""
    table_hit = SearchResultItem(
        chunk_id="child_chunk_002_1",
        document_id="doc_a1",
        parent_chunk_id="parent_chunk_002",
        content="核心交换机",
        section_path="第二章 主要设备选型 / 2.1 核心设备清单",
        rrf_score=0.91
    )

    ctx = await backfiller.backfill(
        session=seeded_rag_session,
        hits=[table_hit],
        tenant_id="t_alpha"
    )

    assert ctx.unique_parent_count == 1
    p = ctx.parents[0]
    assert "| 设备名称 | 品牌型号 | 数量 |" in p.content
    assert "| 核心交换机 | 华为 S6730-H | 2台 |" in p.content


@pytest.mark.asyncio
async def test_backfill_citation_indexing(
    backfiller: ContextBackfiller,
    seeded_rag_session: AsyncSession
):
    """9. 验证证据链锚点具备完备的字段 (页码、得分、章节)"""
    hit = SearchResultItem(
        chunk_id="child_chunk_001_1",
        document_id="doc_a1",
        parent_chunk_id="parent_chunk_001",
        content="本系统采用两路独立 10kV 高压进线",
        section_path="第一章 总体技术方案 / 1.1 供配电系统",
        page_number=1,
        bbox_coordinates={"x0": 50, "y0": 100},
        rrf_score=0.8888
    )

    ctx = await backfiller.backfill(
        session=seeded_rag_session,
        hits=[hit],
        tenant_id="t_alpha"
    )

    cits = ctx.parents[0].child_citations
    assert len(cits) == 1
    c = cits[0]
    assert c.child_chunk_id == "child_chunk_001_1"
    assert c.page_number == 1
    assert c.relevance_score == 0.8888
    assert c.bbox_coordinates == {"x0": 50, "y0": 100}


@pytest.mark.asyncio
async def test_backfill_performance_benchmark(
    backfiller: ContextBackfiller,
    seeded_rag_session: AsyncSession
):
    """10. 验证回填执行耗时基准 (< 50ms)"""
    hits = [
        SearchResultItem(
            chunk_id="child_chunk_001_1",
            document_id="doc_a1",
            parent_chunk_id="parent_chunk_001",
            content="供电系统",
            section_path="1.1",
            rrf_score=0.9
        ),
        SearchResultItem(
            chunk_id="child_chunk_002_1",
            document_id="doc_a1",
            parent_chunk_id="parent_chunk_002",
            content="设备清单",
            section_path="2.1",
            rrf_score=0.8
        )
    ]

    start_t = time.perf_counter()
    ctx = await backfiller.backfill(
        session=seeded_rag_session,
        hits=hits,
        tenant_id="t_alpha"
    )
    elapsed = time.perf_counter() - start_t

    assert elapsed < 0.05, f"回填耗时 {elapsed:.4f}s 超过 50ms 限制"
    assert ctx.unique_parent_count == 2

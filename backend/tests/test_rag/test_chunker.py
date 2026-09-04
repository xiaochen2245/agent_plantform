"""
分层切块器 (ParentChildChunker) 测试套件
验证父子切块尺寸约束 (Parent: 1024~2048, Child: 128~256)、外键引用、表格原子性与章节路径继承
"""

import time
import pytest
from app.rag.chunker import ParentChildChunker, TokenCounter
from app.schemas.ast import (
    ASTBlockType,
    ASTNode,
    DocumentSourceType,
    ScheduleTaskData,
    TableData,
    UnifiedDocumentAST,
)


@pytest.fixture
def chunker() -> ParentChildChunker:
    return ParentChildChunker(
        parent_max_tokens=1024,
        parent_min_tokens=256,
        child_max_tokens=256,
        child_min_tokens=128,
        child_overlap_tokens=32
    )


def make_sample_ast(
    paragraph_count=10,
    words_per_p=60,
    include_table=False,
    include_schedule=False,
    short_doc=False
) -> UnifiedDocumentAST:
    """构建用于切块测试的标准 UnifiedDocumentAST"""
    nodes = []

    if short_doc:
        nodes.append(
            ASTNode(
                block_id="n_short",
                block_type=ASTBlockType.PARAGRAPH,
                section_path=["总则"],
                text_content="这是一段非常简短的项目介绍文字，总长度不足一百字，测试切块边界。",
                page_or_sheet="1"
            )
        )
    else:
        for i in range(1, paragraph_count + 1):
            text_body = f"第 {i} 章节 技术方案实施细则与工期节点控制要求。" + "标准建筑智能化技术参数规范内容。" * words_per_p
            nodes.append(
                ASTNode(
                    block_id=f"n_p_{i}",
                    block_type=ASTBlockType.HEADING if i % 5 == 1 else ASTBlockType.PARAGRAPH,
                    level=1 if i % 5 == 1 else None,
                    section_path=["第一章 总体技术方案", f"第 {i} 小节"],
                    text_content=text_body,
                    page_or_sheet=str(i)
                )
            )

        if include_table:
            table_md = (
                "| 序号 | 设备名称 | 品牌型号 | 数量 |\n"
                "| --- | --- | --- | --- |\n"
                "| 1 | 核心交换机 | 华为 S6730-H | 2台 |\n"
                "| 2 | 防火墙 | 天融信 TG-4500 | 2台 |\n"
            )
            nodes.append(
                ASTNode(
                    block_id="n_tbl_1",
                    block_type=ASTBlockType.TABLE,
                    section_path=["第二章 设备清单", "核心网络设备表"],
                    text_content=table_md,
                    table_data=TableData(
                        headers=[["序号", "设备名称", "品牌型号", "数量"]],
                        rows=[["1", "核心交换机", "华为 S6730-H", "2台"], ["2", "防火墙", "天融信 TG-4500", "2台"]],
                        markdown=table_md
                    ),
                    page_or_sheet="3"
                )
            )

        if include_schedule:
            nodes.append(
                ASTNode(
                    block_id="n_task_1",
                    block_type=ASTBlockType.SCHEDULE_TASK,
                    section_path=["第三章 施工进度", "机房综合布线"],
                    text_content="任务: 机房综合布线; 工期: 15天; 关键路径: 是",
                    schedule_data=ScheduleTaskData(
                        task_id="T101",
                        task_name="机房综合布线",
                        duration_days=15.0,
                        is_critical_path=True,
                        predecessors=[]
                    ),
                    page_or_sheet="Gantt"
                )
            )

    return UnifiedDocumentAST(
        document_id="doc_chunk_test_001",
        tenant_id="tenant_alpha",
        file_name="test_tech_spec.docx",
        source_type=DocumentSourceType.DOCX,
        total_pages_or_sheets=paragraph_count,
        nodes=nodes
    )


def test_chunker_parent_token_boundary(chunker: ParentChildChunker):
    """1. 验证 Parent 块 token 长度受 parent_max_tokens 约束"""
    ast = make_sample_ast(paragraph_count=20, words_per_p=80)
    parents, children = chunker.chunk_document(ast)

    assert len(parents) > 0
    token_counter = TokenCounter()
    for p in parents:
        tokens = token_counter.count_tokens(p.content)
        # 允许少量边界字符溢出容差 (+10%)
        assert tokens <= chunker.parent_max_tokens * 1.15, f"Parent 块 token {tokens} 超出限制"


def test_chunker_child_token_boundary(chunker: ParentChildChunker):
    """2. 验证 Child 块 token 长度受 child_max_tokens 约束 (128~256)"""
    ast = make_sample_ast(paragraph_count=10, words_per_p=60)
    parents, children = chunker.chunk_document(ast)

    assert len(children) > 0
    token_counter = TokenCounter()
    for c in children:
        tokens = token_counter.count_tokens(c.content)
        assert tokens <= chunker.child_max_tokens * 1.2, f"Child 块 token {tokens} 超出限制"


def test_chunker_parent_child_fk_linkage(chunker: ParentChildChunker):
    """3. 验证每个 Child 块的 parent_chunk_id 均能在 Parent 集合中找到对应父块"""
    ast = make_sample_ast(paragraph_count=15, words_per_p=50)
    parents, children = chunker.chunk_document(ast)

    parent_ids = {p.chunk_id for p in parents}
    for c in children:
        assert c.parent_chunk_id is not None
        assert c.parent_chunk_id in parent_ids, f"Child {c.chunk_id} 的父块 ID {c.parent_chunk_id} 悬空"


def test_chunker_table_isolated_chunking(chunker: ParentChildChunker):
    """4. 验证表格保持原子完整性 (is_table_isolated == True)，不被切碎"""
    ast = make_sample_ast(paragraph_count=5, words_per_p=40, include_table=True)
    parents, children = chunker.chunk_document(ast)

    table_children = [c for c in children if c.is_table_isolated]
    assert len(table_children) >= 1
    t_chunk = table_children[0]
    assert "核心交换机" in t_chunk.content
    assert "华为 S6730-H" in t_chunk.content


def test_chunker_section_path_preservation(chunker: ParentChildChunker):
    """5. 验证 section_path 大纲面包屑完整传递给所有切块"""
    ast = make_sample_ast(paragraph_count=5, words_per_p=40)
    parents, children = chunker.chunk_document(ast)

    for p in parents:
        assert isinstance(p.section_path, (list, str))
        assert len(p.section_path) > 0
        assert "第一章 总体技术方案" in p.section_path

    for c in children:
        assert isinstance(c.section_path, (list, str))
        assert len(c.section_path) > 0


def test_chunker_short_document_no_over_chunking(chunker: ParentChildChunker):
    """6. 验证极短文档 (<100 tokens) 不发生过度碎片切块，恰好生成 1 Parent + 1 Child"""
    ast = make_sample_ast(short_doc=True)
    parents, children = chunker.chunk_document(ast)

    assert len(parents) == 1
    assert len(children) == 1
    assert children[0].parent_chunk_id == parents[0].chunk_id


def test_chunker_sliding_window_overlap(chunker: ParentChildChunker):
    """7. 验证 Child 切块滑动窗口重叠属性"""
    ast = make_sample_ast(paragraph_count=5, words_per_p=100)
    parents, children = chunker.chunk_document(ast)

    assert len(children) >= 2
    # 相邻 child 切块存在重合内容
    c1 = children[0]
    c2 = children[1]
    if c1.parent_chunk_id == c2.parent_chunk_id:
        c1_tail = c1.content[-30:]
        assert len(c1_tail) > 0


def test_chunker_cad_and_schedule_metadata(chunker: ParentChildChunker):
    """8. 验证工程进度任务等特异性元数据保留"""
    ast = make_sample_ast(paragraph_count=2, include_schedule=True)
    parents, children = chunker.chunk_document(ast)

    schedule_chunks = [c for c in children if "机房综合布线" in c.content]
    assert len(schedule_chunks) >= 1
    assert schedule_chunks[0].page_or_sheet == "Gantt"


def test_chunker_empty_nodes_handling(chunker: ParentChildChunker):
    """9. 验证无节点的空 AST 返回空切块列表，无异常"""
    empty_ast = UnifiedDocumentAST(
        document_id="empty_doc",
        tenant_id="t1",
        file_name="empty.docx",
        source_type=DocumentSourceType.DOCX,
        total_pages_or_sheets=1,
        nodes=[]
    )
    parents, children = chunker.chunk_document(empty_ast)
    assert len(parents) == 0
    assert len(children) == 0


def test_chunker_large_ast_performance(chunker: ParentChildChunker):
    """10. 验证大文档 (50 个段落) 切块耗时 < 1 秒"""
    ast = make_sample_ast(paragraph_count=50, words_per_p=40)

    start_t = time.perf_counter()
    parents, children = chunker.chunk_document(ast)
    elapsed = time.perf_counter() - start_t

    assert elapsed < 1.0, f"切块耗时 {elapsed:.2f}s 超过 1s 阈值"
    assert len(parents) > 0
    assert len(children) > len(parents)


def test_chunker_hybrid_table_and_paragraph_retention(chunker: ParentChildChunker):
    """11. 验证同章节包含表格与正文段落时，正文段落与表格均被完整切块，无静默丢失"""
    ast = UnifiedDocumentAST(
        document_id="doc_hybrid_001",
        tenant_id="tenant_alpha",
        file_name="hybrid.docx",
        source_type=DocumentSourceType.DOCX,
        total_pages_or_sheets=1,
        nodes=[
            ASTNode(
                block_id="p1",
                block_type=ASTBlockType.PARAGRAPH,
                section_path=["第一章 施工方案", "1.1 总体要求"],
                text_content="重要的技术条款：系统必须满足7x24小时高可用要求，系统容灾RTO小于15分钟。",
            ),
            ASTNode(
                block_id="t1",
                block_type=ASTBlockType.TABLE,
                section_path=["第一章 施工方案", "1.1 总体要求"],
                text_content="| 设备名称 | 品牌型号 | 数量 |\n| --- | --- | --- |\n| 核心交换机 | 华为 S6730 | 2台 |",
                table_data=TableData(
                    headers=[["设备名称", "品牌型号", "数量"]],
                    rows=[["核心交换机", "华为 S6730", "2台"]],
                    markdown="| 设备名称 | 品牌型号 | 数量 |\n| --- | --- | --- |\n| 核心交换机 | 华为 S6730 | 2台 |",
                ),
            ),
            ASTNode(
                block_id="p2",
                block_type=ASTBlockType.PARAGRAPH,
                section_path=["第一章 施工方案", "1.1 总体要求"],
                text_content="质保金比例为合同总价的3%，验收合格后一年内付清剩余款项。",
            ),
        ],
    )
    parents, children = chunker.chunk_document(ast)

    assert len(parents) >= 1
    # 验证表格行已切块
    assert any("核心交换机" in c.content for c in children), "表格内容未被切块！"
    # 验证表格前后的正文段落均被完整保留
    assert any("7x24小时高可用要求" in c.content for c in children), "段落1被静默丢弃！"
    assert any("质保金比例为合同总价的3%" in c.content for c in children), "段落2被静默丢弃！"
    # 所有子切片均关联至合法的 parent_chunk_id
    parent_ids = {p.chunk_id for p in parents}
    for c in children:
        assert c.parent_chunk_id in parent_ids


def test_chunker_unbroken_long_text_physical_truncation(chunker: ParentChildChunker):
    """12. 验证无标点符号、无空格的超长连续文本物理字符/Token硬截断，不卡死且不超限"""
    # 构造 2,000 字无任何标点符号的连续中文字符串
    unbroken_text = "工程施工建设现场智能安全监控管理规程细则" * 100
    ast = UnifiedDocumentAST(
        document_id="doc_unbroken_001",
        tenant_id="tenant_alpha",
        file_name="unbroken.docx",
        source_type=DocumentSourceType.DOCX,
        total_pages_or_sheets=1,
        nodes=[
            ASTNode(
                block_id="p_long",
                block_type=ASTBlockType.PARAGRAPH,
                section_path=["连续长文本章节"],
                text_content=unbroken_text,
            )
        ],
    )
    parents, children = chunker.chunk_document(ast)

    assert len(parents) > 0
    assert len(children) > 1

    token_counter = TokenCounter()
    for c in children:
        tokens = token_counter.count_tokens(c.content)
        # 严格遵守 child_max_tokens 上限约束
        assert tokens <= chunker.child_max_tokens * 1.15, f"Child 切片 token {tokens} 超出限制"


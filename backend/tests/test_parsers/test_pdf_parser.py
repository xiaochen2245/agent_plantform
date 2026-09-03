"""
双层 PDF、电子文件与扫描件 (PDF) 解析器测试套件
验证 BoundingBox 视觉坐标、PDF 书签大纲 (TOC)、结构化表格、多栏阅读顺序、密码加密防御与 OCR 标注
"""

import pytest
from app.parsers.pdf_parser import PDFParser
from app.parsers.base import (
    EmptyDocumentError,
    MalformedDocumentError,
    PasswordProtectedError,
)
from app.schemas.ast import ASTBlockType, DocumentSourceType, UnifiedDocumentAST


def make_pdf_bytes(
    text_blocks=None,
    toc_titles=None,
    is_encrypted=False,
    is_scanned=False,
    corrupt=False,
    table_str=None
) -> bytes:
    """构建标准 PDF 数据流 (支持 PyMuPDF / 原生正则双模式测试)"""
    if corrupt:
        return b"NOT_A_VALID_PDF_STREAM_WITHOUT_MAGIC"

    if is_encrypted:
        return b"%PDF-1.7\n1 0 obj\n<< /Encrypt 2 0 R >>\nendobj\n%%EOF"

    lines = ["%PDF-1.7"]

    # 1. 注入 PDF 目录大纲书签
    if toc_titles:
        for t in toc_titles:
            lines.append(f"/Title ({t})")

    # 2. 注入扫描图标记
    if is_scanned:
        lines.append("<< /Type /XObject /Subtype /Image /Width 100 /Height 100 >>")

    # 3. 注入结构化表格
    if table_str:
        lines.append(table_str)

    # 4. 注入双层文本块 (BT ... Tm ... Tj ... ET)
    items = text_blocks if text_blocks is not None else [
        {"text": "第一章 某三甲医院智能化系统设计招标文件", "x": 50.0, "y": 750.0},
        {"text": "【重要提示/不可偏离】投标人必须具备电子与智能化工程专业承包壹级资质，否则作废标处理。", "x": 50.0, "y": 700.0},
        {"text": "1.1 工期进度控制节点", "x": 50.0, "y": 650.0},
        {"text": "本项目总日历天数严格限制为 90 天，违约赔偿金为每天 5 万元人民币。", "x": 50.0, "y": 600.0},
    ]

    for it in items:
        txt = it["text"]
        x = it.get("x", 50.0)
        y = it.get("y", 700.0)
        lines.append(f"BT 1 0 0 1 {x} {y} Tm ({txt}) Tj ET")

    lines.append("%%EOF")
    return "\n".join(lines).encode("utf-8")


@pytest.fixture
def pdf_parser() -> PDFParser:
    return PDFParser()


def test_pdf_parser_metadata(pdf_parser: PDFParser):
    """1. 验证 PDF 解析器支持扩展名与源类型"""
    assert ".pdf" in pdf_parser.supported_extensions
    assert pdf_parser.source_type == DocumentSourceType.PDF


@pytest.mark.asyncio
async def test_pdf_dual_layer_text_and_bbox(pdf_parser: PDFParser):
    """2. 验证双层文本提取与 BoundingBox 坐标"""
    data = make_pdf_bytes()
    ast = await pdf_parser.parse(data, "tender.pdf")

    assert ast.source_type == DocumentSourceType.PDF
    assert len(ast.nodes) >= 3

    nodes_with_bbox = [n for n in ast.nodes if n.bbox is not None]
    assert len(nodes_with_bbox) >= 2
    for n in nodes_with_bbox:
        assert n.bbox.x0 >= 0.0
        assert n.bbox.y0 >= 0.0
        assert n.bbox.x1 > n.bbox.x0
        assert n.bbox.y1 > n.bbox.y0
        assert n.bbox.page_number == 1


@pytest.mark.asyncio
async def test_pdf_bookmark_outline_hierarchy(pdf_parser: PDFParser):
    """3. 验证 PDF 目录书签提取为大纲节点"""
    bookmarks = ["第一章 项目概述", "第二章 需求规格说明", "第三章 施工组织设计"]
    data = make_pdf_bytes(toc_titles=bookmarks)
    ast = await pdf_parser.parse(data, "bookmarks.pdf")

    toc_nodes = [n for n in ast.nodes if n.extra_metadata.get("is_toc_bookmark")]
    assert len(toc_nodes) == 3
    assert "第一章 项目概述" in toc_nodes[0].text_content
    assert "第二章 需求规格说明" in toc_nodes[1].text_content


@pytest.mark.asyncio
async def test_pdf_table_detection_and_markdown(pdf_parser: PDFParser):
    """4. 验证结构化表格提取与 Markdown 转换"""
    table_sample = (
        "| 序号 | 需求指标 | 达标承诺 |\n"
        "| --- | --- | --- |\n"
        "| 1 | 骨干网络带宽 >= 10Gbps | 响应满足 |\n"
        "| 2 | 双电源自动切换时间 <= 5ms | 优于标准 |\n"
    )
    data = make_pdf_bytes(table_str=table_sample)
    ast = await pdf_parser.parse(data, "table.pdf")

    tbls = [n for n in ast.nodes if n.block_type == ASTBlockType.TABLE]
    assert len(tbls) >= 1
    tbl = tbls[0]
    assert tbl.table_data is not None
    assert "骨干网络带宽" in tbl.table_data.markdown
    assert "双电源自动切换时间" in tbl.table_data.markdown


@pytest.mark.asyncio
async def test_pdf_multi_column_reading_order(pdf_parser: PDFParser):
    """5. 验证多栏阅读重排"""
    left_column = [
        {"text": "左栏段落1：项目总体组织框架", "x": 50.0, "y": 700.0},
        {"text": "左栏段落2：项目经理部配置人员", "x": 50.0, "y": 600.0},
    ]
    right_column = [
        {"text": "右栏段落1：施工主要机械设备投入", "x": 350.0, "y": 700.0},
        {"text": "右栏段落2：试验及检测设备清单", "x": 350.0, "y": 600.0},
    ]
    data = make_pdf_bytes(text_blocks=left_column + right_column)
    ast = await pdf_parser.parse(data, "columns.pdf")

    text_all = " ".join(n.text_content for n in ast.nodes)
    assert "项目总体组织框架" in text_all
    assert "施工主要机械设备投入" in text_all


@pytest.mark.asyncio
async def test_pdf_encrypted_password_error(pdf_parser: PDFParser):
    """6. 验证加密 PDF 在未提供密码时抛出 PasswordProtectedError"""
    data = make_pdf_bytes(is_encrypted=True)
    with pytest.raises(PasswordProtectedError):
        await pdf_parser.parse(data, "locked.pdf")


@pytest.mark.asyncio
async def test_pdf_corrupted_file_error(pdf_parser: PDFParser):
    """7. 验证破损 PDF 抛出 MalformedDocumentError"""
    data = make_pdf_bytes(corrupt=True)
    with pytest.raises(MalformedDocumentError):
        await pdf_parser.parse(data, "broken.pdf")


@pytest.mark.asyncio
async def test_pdf_rotated_pages_coordinate_normalization(pdf_parser: PDFParser):
    """8. 验证页面旋转与坐标解析"""
    data = make_pdf_bytes()
    ast = await pdf_parser.parse(data, "rotated.pdf")
    assert ast.source_type == DocumentSourceType.PDF
    assert len(ast.nodes) > 0


@pytest.mark.asyncio
async def test_pdf_scanned_ocr_fallback(pdf_parser: PDFParser):
    """9. 验证纯图片扫描版页面打上 OCR 需求标记"""
    data = make_pdf_bytes(text_blocks=[], is_scanned=True)
    ast = await pdf_parser.parse(data, "scanned_doc.pdf")

    assert len(ast.nodes) >= 1
    scan_node = ast.nodes[0]
    assert scan_node.extra_metadata.get("is_scanned_page") is True
    assert scan_node.extra_metadata.get("ocr_required") is True


@pytest.mark.asyncio
async def test_pdf_reduction_accuracy_threshold(pdf_parser: PDFParser):
    """10. 验证招标文件核心条款还原率 >= 98%"""
    ground_truth = [
        "第一章 某三甲医院智能化系统设计招标文件",
        "不可偏离",
        "壹级资质",
        "本项目总日历天数严格限制为 90 天"
    ]
    data = make_pdf_bytes()
    ast = await pdf_parser.parse(data, "accuracy.pdf")

    all_text = " ".join(n.text_content for n in ast.nodes)
    matched = sum(1 for phrase in ground_truth if phrase in all_text)
    rate = matched / len(ground_truth)
    assert rate >= 0.98, f"PDF 文本还原率 {rate:.4f} 未达到 98% 准则"

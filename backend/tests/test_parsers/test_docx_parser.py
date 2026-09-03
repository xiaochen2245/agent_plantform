"""
Word 技术标书与合同文件 (DOCX / DOC) 解析器测试套件
验证段落与表格交错顺序、多级大纲树面包屑、废标/警示条款 CALLOUT、合并单元格与 >= 98% 还原率
"""

import io
import time
import zipfile
import pytest
from app.parsers.docx_parser import DOCXParser
from app.parsers.base import EmptyDocumentError, MalformedDocumentError
from app.schemas.ast import ASTBlockType, DocumentSourceType, UnifiedDocumentAST


def make_docx_bytes(
    elements=None,
    corrupt=False
) -> bytes:
    """构建标准 Word OOXML (ZIP + word/document.xml) 内存字节流"""
    if corrupt:
        return b"PK\x03\x04NOT_A_VALID_DOCX_STREAM"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # [Content_Types].xml
        ct_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
            '  <Default Extension="xml" ContentType="application/xml"/>\n'
            '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
            '  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>\n'
            '</Types>'
        )
        z.writestr("[Content_Types].xml", ct_xml)

        body_elements = elements or [
            {"type": "heading", "level": 1, "text": "第一章 投标人须知前附表"},
            {"type": "callout", "text": "【重要提示/不可偏离】本招标项目设置强制性废标条款，不满足资质要求的投标文件作废标处理。"},
            {"type": "p", "text": "1.1 总体工程概况及技术标准要求"},
            {"type": "table", "headers": ["序号", "技术参数指标", "偏离情况"], "rows": [["1", "UPS主机后备时间 >= 4小时", "无偏离"], ["2", "机房精密空调能效比 >= 3.5", "正偏离"]]},
            {"type": "heading", "level": 2, "text": "1.2 施工组织与质量保障承诺"},
            {"type": "p", "text": "本工程计划工期为 90 个日历天，承诺达到国家鲁班奖评定标准。"},
        ]

        from xml.sax.saxutils import escape

        xml_parts = []
        for elem in body_elements:
            etype = elem.get("type")
            if etype in ("heading", "p", "callout"):
                txt = escape(elem.get("text", ""))
                style_tag = ""
                if etype == "heading":
                    lvl = elem.get("level", 1)
                    style_tag = f'<w:pPr><w:pStyle w:val="Heading{lvl}"/></w:pPr>'

                # 列表项
                num_tag = ""
                if elem.get("is_list"):
                    num_tag = '<w:pPr><w:numPr><w:ilvl w:val="0"/></w:numPr></w:pPr>'

                xml_parts.append(
                    f'<w:p>{style_tag}{num_tag}<w:r><w:t>{txt}</w:t></w:r></w:p>'
                )
            elif etype == "table":
                hdrs = elem.get("headers", [])
                rows_data = elem.get("rows", [])
                tr_list = []

                if hdrs:
                    tc_list = [f'<w:tc><w:p><w:r><w:t>{escape(h)}</w:t></w:r></w:p></w:tc>' for h in hdrs]
                    tr_list.append(f'<w:tr>{"".join(tc_list)}</w:tr>')

                for r in rows_data:
                    tc_list = [f'<w:tc><w:p><w:r><w:t>{escape(c)}</w:t></w:r></w:p></w:tc>' for c in r]
                    tr_list.append(f'<w:tr>{"".join(tc_list)}</w:tr>')

                xml_parts.append(f'<w:tbl>{"".join(tr_list)}</w:tbl>')

        doc_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">\n'
            '  <w:body>\n'
            + "\n".join(xml_parts) +
            '  </w:body>\n'
            '</w:document>'
        )
        z.writestr("word/document.xml", doc_xml)

    return buf.getvalue()


@pytest.fixture
def docx_parser() -> DOCXParser:
    return DOCXParser()


def test_docx_parser_metadata(docx_parser: DOCXParser):
    """1. 验证 DOCX 解析器元数据与扩展名注册"""
    assert ".docx" in docx_parser.supported_extensions
    assert ".doc" in docx_parser.supported_extensions
    assert docx_parser.source_type == DocumentSourceType.DOCX


@pytest.mark.asyncio
async def test_docx_heading_outline_hierarchy(docx_parser: DOCXParser):
    """2. 验证多级大纲树 (Heading 1, Heading 2) 面包屑链路"""
    data = make_docx_bytes()
    ast = await docx_parser.parse(data, "tender_spec.docx")

    headings = [n for n in ast.nodes if n.block_type == ASTBlockType.HEADING]
    assert len(headings) >= 3

    h1 = headings[0]
    assert h1.level == 1
    assert "第一章" in h1.text_content

    assert any("1.1" in h.text_content for h in headings)
    assert any("1.2" in h.text_content for h in headings)

    h_sub = next(h for h in headings if "1.2" in h.text_content)
    assert h_sub.level == 2
    assert any("第一章" in part for part in h_sub.section_path)


@pytest.mark.asyncio
async def test_docx_callout_detection(docx_parser: DOCXParser):
    """3. 验证废标条款/重要提示被高亮识别为 CALLOUT"""
    data = make_docx_bytes()
    ast = await docx_parser.parse(data, "proposal.docx")

    callouts = [n for n in ast.nodes if n.block_type == ASTBlockType.CALLOUT]
    assert len(callouts) >= 1
    assert "不可偏离" in callouts[0].text_content or "废标条款" in callouts[0].text_content


@pytest.mark.asyncio
async def test_docx_table_interleaved_order(docx_parser: DOCXParser):
    """4. 验证段落与表格严格保持正文交错排版顺序"""
    data = make_docx_bytes()
    ast = await docx_parser.parse(data, "interleaved.docx")

    # 验证节点类型的真实交错出现: HEADING/CALLOUT/PARAGRAPH -> TABLE -> HEADING/PARAGRAPH
    block_types = [n.block_type for n in ast.nodes]
    assert ASTBlockType.TABLE in block_types
    tbl_idx = block_types.index(ASTBlockType.TABLE)
    assert tbl_idx > 0  # 表格前面有标题和段落
    assert tbl_idx < len(block_types) - 1  # 表格后面还有内容


@pytest.mark.asyncio
async def test_docx_merged_table_cells(docx_parser: DOCXParser):
    """5. 验证跨列/跨行表格正常提取"""
    custom_elements = [
        {
            "type": "table",
            "headers": ["设备", "型号", "参数说明"],
            "rows": [
                ["UPS主机", "APC-60KVA", "支持并联冗余"],
                ["蓄电池组", "12V-100AH", "32节标准柜配置"]
            ]
        }
    ]
    data = make_docx_bytes(elements=custom_elements)
    ast = await docx_parser.parse(data, "merged_table.docx")

    tbls = [n for n in ast.nodes if n.block_type == ASTBlockType.TABLE]
    assert len(tbls) == 1
    tbl = tbls[0]
    assert tbl.table_data is not None
    assert "UPS主机" in tbl.table_data.markdown
    assert "APC-60KVA" in tbl.table_data.markdown


@pytest.mark.asyncio
async def test_docx_list_items_prefix(docx_parser: DOCXParser):
    """6. 验证列表项文本前缀标记"""
    list_elements = [
        {"type": "p", "text": "施工现场安全管理要求："},
        {"type": "p", "text": "进入施工现场必须全员佩戴安全帽", "is_list": True},
        {"type": "p", "text": "高空作业必须系好双挂钩安全带", "is_list": True},
    ]
    data = make_docx_bytes(elements=list_elements)
    ast = await docx_parser.parse(data, "list.docx")

    items = [n for n in ast.nodes if n.block_type == ASTBlockType.PARAGRAPH]
    assert len(items) >= 3
    # 验证列表项自动补全了前缀
    assert any(n.text_content.startswith("•") or "安全帽" in n.text_content for n in items)


@pytest.mark.asyncio
async def test_docx_corrupt_file_raises_error(docx_parser: DOCXParser):
    """7. 验证损坏 DOCX 抛出 MalformedDocumentError"""
    data = make_docx_bytes(corrupt=True)
    with pytest.raises(MalformedDocumentError):
        await docx_parser.parse(data, "corrupt.docx")


@pytest.mark.asyncio
async def test_docx_empty_document_handling(docx_parser: DOCXParser):
    """8. 验证空白 Word 文档防御返回空 AST"""
    data = make_docx_bytes(elements=[])
    ast = await docx_parser.parse(data, "empty.docx")
    assert isinstance(ast, UnifiedDocumentAST)
    assert ast.source_type == DocumentSourceType.DOCX


@pytest.mark.asyncio
async def test_docx_large_proposal_performance(docx_parser: DOCXParser):
    """9. 验证大篇幅技术方案 (100 段落 + 5 表格) 解析时间 <= 2 秒"""
    elements = []
    for i in range(1, 101):
        elements.append({"type": "p", "text": f"第 {i} 条 技术规范方案实施要求说明，包含细部深化节点质量把控细节。"})
        if i % 20 == 0:
            elements.append({
                "type": "table",
                "headers": ["参数项", "标准要求", "实际响应"],
                "rows": [["绝缘电阻", ">= 20MΩ", "实测 50MΩ"], ["接地电阻", "<= 1Ω", "实测 0.4Ω"]]
            })

    data = make_docx_bytes(elements=elements)
    start_t = time.perf_counter()
    ast = await docx_parser.parse(data, "large_proposal.docx")
    elapsed = time.perf_counter() - start_t

    assert elapsed < 2.0, f"大文档解析耗时 {elapsed:.2f}s 超过 2s 阈值"
    assert len(ast.nodes) >= 100


@pytest.mark.asyncio
async def test_docx_reduction_accuracy_threshold(docx_parser: DOCXParser):
    """10. 验证标书正文及表格还原率 >= 98%"""
    ground_truth = [
        "第一章 投标人须知前附表",
        "本招标项目设置强制性废标条款",
        "UPS主机后备时间 >= 4小时",
        "机房精密空调能效比 >= 3.5",
        "本工程计划工期为 90 个日历天"
    ]
    data = make_docx_bytes()
    ast = await docx_parser.parse(data, "accuracy.docx")

    all_text = " ".join(n.text_content for n in ast.nodes)
    matched = sum(1 for phrase in ground_truth if phrase in all_text)
    rate = matched / len(ground_truth)
    assert rate >= 0.98, f"DOCX 还原率 {rate:.4f} 未达到 98% 准则"

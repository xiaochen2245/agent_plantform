"""
PPTX / 汇报方案与答辩演示文稿解析器测试套件
验证幻灯片大纲层级、嵌入表格、演讲者备注 (SPEAKER_NOTE)、离屏草稿形状过滤及 >= 98% 还原率
"""

import io
import zipfile
import pytest
from xml.sax.saxutils import escape
from app.parsers.pptx_parser import PPTXParser
from app.parsers.base import EmptyDocumentError, MalformedDocumentError
from app.schemas.ast import ASTBlockType, DocumentSourceType, UnifiedDocumentAST


def make_pptx_bytes(
    slides=None,
    corrupt=False,
    empty=False
) -> bytes:
    """构建标准 PowerPoint OOXML (ZIP + ppt/slides/ + ppt/notesSlides/) 内存字节流"""
    if corrupt:
        return b"PK\x03\x04BAD_PPTX_ARCHIVE"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # [Content_Types].xml
        ct_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
            '  <Default Extension="xml" ContentType="application/xml"/>\n'
            '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
            '  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>\n'
            '</Types>'
        )
        z.writestr("[Content_Types].xml", ct_xml)

        if empty:
            slides_data = []
        else:
            slides_data = slides or [
            {
                "title": "大型三甲医院智能化系统工程设计方案答辩",
                "texts": [
                    {"text": "总体设计理念：安全可靠、智慧互联、绿色节能、平疫结合", "x": 100, "y": 200},
                    {"text": "【草稿离屏备注】请勿向评委展示", "x": -5000, "y": -5000},  # 离屏形状
                ],
                "table": {
                    "headers": ["子系统", "技术指标", "优势特点"],
                    "rows": [["智慧门诊", "排队叫号响应 < 1s", "全流程无感就医"], ["安全防范", "全景高清4K安防", "AI人脸快速识别"]]
                },
                "speaker_note": "汇报重点：向专家评委着重强调我方拥有 20 项国家级三甲医院智能化建设成功案例，工期 90 天具有确定性保障。"
            },
            {
                "title": "施工组织部署与进度控制总计划",
                "texts": [
                    {"text": "工期控制红线：全周期控制在 90 个日历天之内。", "x": 100, "y": 200},
                    {"text": "劳动力组织：高峰期进场技术工人不少于 120 人。", "x": 100, "y": 300},
                ],
                "table": None,
                "speaker_note": "此处应对工期质疑：提前完成了 BIM 深化设计建模，管线碰撞率降低至 0.01%。"
            }
        ]

        for s_idx, s_info in enumerate(slides_data, start=1):
            s_title = escape(s_info.get("title", f"Slide {s_idx}"))
            title_sp = f"""
            <p:sp>
                <p:nvSpPr><p:cNvPr id="1" name="Title"/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>
                <p:spPr><a:xfrm><a:off x="50" y="50"/><a:ext cx="800" cy="80"/></a:xfrm></p:spPr>
                <p:txBody><a:bodyPr/><a:p><a:r><a:t>{s_title}</a:t></a:r></a:p></p:txBody>
            </p:sp>
            """

            sp_list = [title_sp]
            for t_item in s_info.get("texts", []):
                t_val = escape(t_item["text"])
                x_val = t_item.get("x", 100)
                y_val = t_item.get("y", 200)
                sp_list.append(f"""
                <p:sp>
                    <p:nvSpPr><p:cNvPr id="2" name="TextBox"/><p:nvPr/></p:nvSpPr>
                    <p:spPr><a:xfrm><a:off x="{x_val}" y="{y_val}"/><a:ext cx="600" cy="50"/></a:xfrm></p:spPr>
                    <p:txBody><a:bodyPr/><a:p><a:r><a:t>{t_val}</a:t></a:r></a:p></p:txBody>
                </p:sp>
                """)

            tbl_info = s_info.get("table")
            tbl_xml = ""
            if tbl_info:
                headers = tbl_info.get("headers", [])
                rows_data = tbl_info.get("rows", [])
                tr_list = []
                if headers:
                    tc_list = [f'<a:tc><a:txBody><a:p><a:r><a:t>{escape(h)}</a:t></a:r></a:p></a:txBody></a:tc>' for h in headers]
                    tr_list.append(f'<a:tr>{"".join(tc_list)}</a:tr>')
                for r in rows_data:
                    tc_list = [f'<a:tc><a:txBody><a:p><a:r><a:t>{escape(c)}</a:t></a:r></a:p></a:txBody></a:tc>' for c in r]
                    tr_list.append(f'<a:tr>{"".join(tc_list)}</a:tr>')

                tbl_xml = f"""
                <p:graphicFrame>
                    <a:graphic>
                        <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table">
                            <a:tbl>
                                {"".join(tr_list)}
                            </a:tbl>
                        </a:graphicData>
                    </a:graphic>
                </p:graphicFrame>
                """

            slide_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
            <p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
                   xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
                <p:cSld>
                    <p:spTree>
                        {"".join(sp_list)}
                        {tbl_xml}
                    </p:spTree>
                </p:cSld>
            </p:sld>
            """
            z.writestr(f"ppt/slides/slide{s_idx}.xml", slide_xml)

            # 演讲者备注 notesSlide
            note_content = s_info.get("speaker_note")
            if note_content:
                escaped_note = escape(note_content)
                note_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
                <p:notes xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
                         xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
                    <p:cSld>
                        <p:spTree>
                            <p:sp>
                                <p:nvSpPr><p:cNvPr id="1" name="Notes Placeholder"/><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr>
                                <p:txBody><a:bodyPr/><a:p><a:r><a:t>{escaped_note}</a:t></a:r></a:p></p:txBody>
                            </p:sp>
                        </p:spTree>
                    </p:cSld>
                </p:notes>
                """
                z.writestr(f"ppt/notesSlides/notesSlide{s_idx}.xml", note_xml)

    return buf.getvalue()


@pytest.fixture
def pptx_parser() -> PPTXParser:
    return PPTXParser()


def test_pptx_parser_metadata(pptx_parser: PPTXParser):
    """1. 验证 PPTX 解析器支持的扩展名与源类型"""
    assert ".pptx" in pptx_parser.supported_extensions
    assert ".ppt" in pptx_parser.supported_extensions
    assert pptx_parser.source_type == DocumentSourceType.PPTX


@pytest.mark.asyncio
async def test_pptx_slide_titles_extraction(pptx_parser: PPTXParser):
    """2. 验证幻灯片大纲标题准确提取为 ASTBlockType.HEADING"""
    data = make_pptx_bytes()
    ast = await pptx_parser.parse(data, "presentation.pptx")

    headings = [n for n in ast.nodes if n.block_type == ASTBlockType.HEADING]
    assert len(headings) >= 2
    assert "智能化系统工程设计方案答辩" in headings[0].text_content
    assert "施工组织部署与进度控制总计划" in headings[1].text_content


@pytest.mark.asyncio
async def test_pptx_speaker_notes_extraction(pptx_parser: PPTXParser):
    """3. 验证演讲者备注提取为 ASTBlockType.SPEAKER_NOTE"""
    data = make_pptx_bytes()
    ast = await pptx_parser.parse(data, "presentation.pptx")

    notes = [n for n in ast.nodes if n.block_type == ASTBlockType.SPEAKER_NOTE]
    assert len(notes) >= 2

    assert "汇报重点" in notes[0].text_content
    assert "国家级三甲医院智能化建设成功案例" in notes[0].text_content
    assert "管线碰撞率降低至 0.01%" in notes[1].text_content


@pytest.mark.asyncio
async def test_pptx_table_shapes_markdown(pptx_parser: PPTXParser):
    """4. 验证 PPT 嵌入表格提取为 ASTBlockType.TABLE 与 TableData"""
    data = make_pptx_bytes()
    ast = await pptx_parser.parse(data, "presentation.pptx")

    tbls = [n for n in ast.nodes if n.block_type == ASTBlockType.TABLE]
    assert len(tbls) >= 1

    tbl = tbls[0]
    assert tbl.table_data is not None
    assert "智慧门诊" in tbl.table_data.markdown
    assert "排队叫号响应 < 1s" in tbl.table_data.markdown


@pytest.mark.asyncio
async def test_pptx_shapes_reading_order(pptx_parser: PPTXParser):
    """5. 验证幻灯片内形状顺序与内容提取"""
    data = make_pptx_bytes()
    ast = await pptx_parser.parse(data, "presentation.pptx")

    assert len(ast.nodes) >= 5
    assert any("安全可靠" in n.text_content for n in ast.nodes)


@pytest.mark.asyncio
async def test_pptx_grouped_shapes_extraction(pptx_parser: PPTXParser):
    """6. 验证幻灯片多形状元素解析"""
    data = make_pptx_bytes()
    ast = await pptx_parser.parse(data, "shapes.pptx")

    paragraphs = [n for n in ast.nodes if n.block_type == ASTBlockType.PARAGRAPH]
    assert len(paragraphs) >= 2


@pytest.mark.asyncio
async def test_pptx_off_canvas_shapes_filtered(pptx_parser: PPTXParser):
    """7. 验证离屏草稿形状 (坐标为负数) 得到有效过滤"""
    data = make_pptx_bytes()
    ast = await pptx_parser.parse(data, "off_canvas.pptx")

    all_text = " ".join(n.text_content for n in ast.nodes)
    assert "【草稿离屏备注】请勿向评委展示" not in all_text


@pytest.mark.asyncio
async def test_pptx_empty_presentation_handling(pptx_parser: PPTXParser):
    """8. 验证空 PPTX 归档平稳解析"""
    data = make_pptx_bytes(empty=True)
    ast = await pptx_parser.parse(data, "empty.pptx")
    assert isinstance(ast, UnifiedDocumentAST)
    assert ast.source_type == DocumentSourceType.PPTX


@pytest.mark.asyncio
async def test_pptx_corrupt_file_raises_error(pptx_parser: PPTXParser):
    """9. 验证损坏 PPTX 归档抛出 MalformedDocumentError"""
    data = make_pptx_bytes(corrupt=True)
    with pytest.raises(MalformedDocumentError):
        await pptx_parser.parse(data, "corrupt.pptx")


@pytest.mark.asyncio
async def test_pptx_reduction_accuracy_threshold(pptx_parser: PPTXParser):
    """10. 验证汇报方案核心论点与演讲者备注还原率 >= 98%"""
    ground_truth = [
        "大型三甲医院智能化系统工程设计方案答辩",
        "安全可靠、智慧互联、绿色节能、平疫结合",
        "智慧门诊",
        "国家级三甲医院智能化建设成功案例",
        "施工组织部署与进度控制总计划",
        "全周期控制在 90 个日历天之内"
    ]
    data = make_pptx_bytes()
    ast = await pptx_parser.parse(data, "accuracy.pptx")

    extracted = " ".join(n.text_content for n in ast.nodes)
    matched = sum(1 for phrase in ground_truth if phrase in extracted)
    rate = matched / len(ground_truth)
    assert rate >= 0.98, f"PPTX 文本还原率 {rate:.4f} 未达到 98% 准则"

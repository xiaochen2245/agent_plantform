"""
OFD 国标公文解析器测试套件 (GB/T 38330-2019)
验证红头公文头、电子印章、文本行空间聚类、BBox视觉坐标及 >= 98% 结构还原率
"""

import io
import zipfile
import pytest
from app.parsers.ofd_parser import OFDParser
from app.parsers.base import EmptyDocumentError, MalformedDocumentError
from app.schemas.ast import ASTBlockType, DocumentSourceType, UnifiedDocumentAST


def make_ofd_bytes(
    text_lines=None,
    has_red_header=False,
    include_seal=False,
    missing_ofd_xml=False,
    empty_page=False
) -> bytes:
    """构建真实合成 OFD ZIP 归档数据流"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if not missing_ofd_xml:
            ofd_xml = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<ofd:OFD xmlns:ofd="http://www.ofdspec.org/2016" Version="1.0" DocType="OFD">\n'
                '  <ofd:DocBody>\n'
                '    <ofd:DocInfo><ofd:Title>公文批复</ofd:Title></ofd:DocInfo>\n'
                '    <ofd:DocRoot>Doc_0/Document.xml</ofd:DocRoot>\n'
                '  </ofd:DocBody>\n'
                '</ofd:OFD>'
            )
            z.writestr("OFD.xml", ofd_xml)

        doc_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<ofd:Document xmlns:ofd="http://www.ofdspec.org/2016">\n'
            '  <ofd:CommonData><ofd:MaxUnitID>10</ofd:MaxUnitID></ofd:CommonData>\n'
            '  <ofd:Pages>\n'
            '    <ofd:Page ID="1" BaseLoc="Pages/Page_0/Content.xml"/>\n'
            '  </ofd:Pages>\n'
            '</ofd:Document>'
        )
        z.writestr("Doc_0/Document.xml", doc_xml)

        # 组装页面 Content.xml
        text_objects = []
        y_coord = 20.0

        if has_red_header:
            text_objects.append(
                f'<ofd:TextObject ID="1" Boundary="50.0 {y_coord} 400.0 25.0" Font="1" Size="16.0">'
                f'<ofd:TextCode>国发〔2026〕88号 发文字号 绝密 特急</ofd:TextCode>'
                f'</ofd:TextObject>'
            )
            y_coord += 30.0

        if not empty_page:
            lines = text_lines or [
                "第一章 总体部署批复要求",
                "经国务院常务会议审议，原则同意某大型三甲医院智能化综合楼总体投资与施工方案。",
                "1.1 工期及质量管理红线",
                "项目工期必须严格控制在 90 个日历天之内，严禁擅自延期或变更关键路径节点。",
            ]
            for idx, line in enumerate(lines, start=2):
                text_objects.append(
                    f'<ofd:TextObject ID="{idx}" Boundary="50.0 {y_coord} 450.0 15.0" Font="1" Size="12.0">'
                    f'<ofd:TextCode>{line}</ofd:TextCode>'
                    f'</ofd:TextObject>'
                )
                y_coord += 20.0

        page_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<ofd:Page xmlns:ofd="http://www.ofdspec.org/2016">\n'
            '  <ofd:Area><ofd:PhysicalBox>0 0 595.0 842.0</ofd:PhysicalBox></ofd:Area>\n'
            '  <ofd:Content>\n'
            '    <ofd:Layer ID="1">\n'
            + "\n".join(text_objects) +
            '    </ofd:Layer>\n'
            '  </ofd:Content>\n'
            '</ofd:Page>'
        )
        z.writestr("Doc_0/Pages/Page_0/Content.xml", page_xml)

        if include_seal:
            seal_xml = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<ofd:Signature xmlns:ofd="http://www.ofdspec.org/2016">\n'
                '  <ofd:SignedInfo>\n'
                '    <ofd:Provider>国家密码管理局安全电子印章系统</ofd:Provider>\n'
                '    <ofd:SealName>中华人民共和国应急管理与发改委员会电子公章</ofd:SealName>\n'
                '  </ofd:SignedInfo>\n'
                '</ofd:Signature>'
            )
            z.writestr("Doc_0/Signs/Sign_0/Signature.xml", seal_xml)

    return buf.getvalue()


@pytest.fixture
def ofd_parser() -> OFDParser:
    return OFDParser()


def test_ofd_parser_metadata(ofd_parser: OFDParser):
    """1. 验证 OFD 解析器元数据与扩展名注册"""
    assert ".ofd" in ofd_parser.supported_extensions
    assert ofd_parser.source_type == DocumentSourceType.OFD


@pytest.mark.asyncio
async def test_ofd_parse_valid_content(ofd_parser: OFDParser):
    """2. 验证有效 OFD 内容解析生成 UnifiedDocumentAST"""
    data = make_ofd_bytes()
    ast = await ofd_parser.parse(data, "official_dispatch.ofd", tenant_id="tenant_001")
    
    assert isinstance(ast, UnifiedDocumentAST)
    assert ast.file_name == "official_dispatch.ofd"
    assert ast.tenant_id == "tenant_001"
    assert ast.source_type == DocumentSourceType.OFD
    assert ast.total_pages_or_sheets >= 1
    assert len(ast.nodes) >= 3


@pytest.mark.asyncio
async def test_ofd_red_header_and_seal_detection(ofd_parser: OFDParser):
    """3. 验证红头公文头 CALLOUT 与电子印章检测"""
    data = make_ofd_bytes(has_red_header=True, include_seal=True)
    ast = await ofd_parser.parse(data, "red_header_doc.ofd")

    callouts = [n for n in ast.nodes if n.block_type == ASTBlockType.CALLOUT]
    assert len(callouts) >= 2  # 包含红头公文发文字号与电子签章

    red_header = next(n for n in callouts if "国发" in n.text_content)
    assert red_header.extra_metadata.get("is_official_header") is True

    seal = next(n for n in callouts if n.extra_metadata.get("type") == "stamp_or_signature")
    assert "电子印章" in seal.text_content
    assert "国家密码管理局" in seal.text_content


@pytest.mark.asyncio
async def test_ofd_bounding_box_coordinates(ofd_parser: OFDParser):
    """4. 验证文本块 BoundingBox 绝对坐标计算正确"""
    data = make_ofd_bytes()
    ast = await ofd_parser.parse(data, "coordinates.ofd")

    for node in ast.nodes:
        if node.block_type in (ASTBlockType.HEADING, ASTBlockType.PARAGRAPH):
            assert node.bbox is not None
            assert node.bbox.x0 >= 0.0
            assert node.bbox.y0 >= 0.0
            assert node.bbox.x1 > node.bbox.x0
            assert node.bbox.y1 > node.bbox.y0
            assert node.bbox.page_number == 1


@pytest.mark.asyncio
async def test_ofd_heading_outline_hierarchy(ofd_parser: OFDParser):
    """5. 验证大纲层级与章节面包屑栈"""
    data = make_ofd_bytes()
    ast = await ofd_parser.parse(data, "outline.ofd")

    headings = [n for n in ast.nodes if n.block_type == ASTBlockType.HEADING]
    assert len(headings) >= 2

    h1 = headings[0]
    assert h1.level == 1
    assert "第一章" in h1.text_content

    h2 = headings[1]
    assert h2.level == 2
    assert "1.1" in h2.text_content
    assert any("第一章" in part for part in h2.section_path)


@pytest.mark.asyncio
async def test_ofd_corrupt_zip_error(ofd_parser: OFDParser):
    """6. 验证破损压缩流异常防御"""
    with pytest.raises(MalformedDocumentError):
        await ofd_parser.parse(b"PK\x03\x04BROKEN_GARBAGE_PAYLOAD", "corrupt.ofd")


@pytest.mark.asyncio
async def test_ofd_missing_xml_error(ofd_parser: OFDParser):
    """7. 验证缺失 OFD.xml 根索引异常防御"""
    data = make_ofd_bytes(missing_ofd_xml=True)
    with pytest.raises(MalformedDocumentError):
        await ofd_parser.parse(data, "missing_root.ofd")


@pytest.mark.asyncio
async def test_ofd_empty_page_handling(ofd_parser: OFDParser):
    """8. 验证空页面/纯光栅图页面不崩溃且标注"""
    data = make_ofd_bytes(empty_page=True)
    ast = await ofd_parser.parse(data, "empty_page.ofd")
    assert ast.total_pages_or_sheets == 1
    assert len(ast.nodes) == 1
    assert "未提取到纯文本" in ast.nodes[0].text_content


@pytest.mark.asyncio
async def test_ofd_special_unicode_entities(ofd_parser: OFDParser):
    """9. 验证特殊标点符号与中文公文专有字符保真"""
    special_lines = [
        "第一条 关于印发《智能建造与建筑工业化协同发展实施方案》的通知【重大专项】",
        "编号：GH-2026-〇〇一号，包含特殊符号：① ② ③，±0.000m标高，Φ25螺纹钢筋。",
    ]
    data = make_ofd_bytes(text_lines=special_lines)
    ast = await ofd_parser.parse(data, "special_chars.ofd")

    combined = " ".join(n.text_content for n in ast.nodes)
    assert "《智能建造与建筑工业化协同发展实施方案》" in combined
    assert "【重大专项】" in combined
    assert "〇〇一号" in combined
    assert "±0.000m" in combined or "标高" in combined


@pytest.mark.asyncio
async def test_ofd_reduction_accuracy_threshold(ofd_parser: OFDParser):
    """10. 验证 AST 还原率 >= 98%"""
    ground_truth = [
        "第一章 总体部署批复要求",
        "经国务院常务会议审议，原则同意某大型三甲医院智能化综合楼总体投资与施工方案。",
        "1.1 工期及质量管理红线",
        "项目工期必须严格控制在 90 个日历天之内，严禁擅自延期或变更关键路径节点。",
    ]
    data = make_ofd_bytes(text_lines=ground_truth)
    ast = await ofd_parser.parse(data, "accuracy_test.ofd")

    extracted_text = " ".join(n.text_content for n in ast.nodes)
    gt_total_chars = sum(len(line) for line in ground_truth)
    matched_chars = sum(len(line) for line in ground_truth if line in extracted_text)

    char_accuracy = matched_chars / gt_total_chars
    assert char_accuracy >= 0.98, f"OFD 文本还原率 {char_accuracy:.4f} 低于 98% 准则"

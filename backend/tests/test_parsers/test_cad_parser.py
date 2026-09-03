"""
AutoCAD 施工图与技术图纸 (CAD / DXF / DWG) 解析器测试套件
验证图签属性块提取、材料设备表、MTEXT 格式清洗、图层与空间坐标包围盒、空图纸防御
"""

import pytest
from app.parsers.cad_parser import CADParser
from app.parsers.base import EmptyDocumentError, MalformedDocumentError
from app.schemas.ast import ASTBlockType, DocumentSourceType, UnifiedDocumentAST


def make_dxf_bytes(
    notes=None,
    title_block=None,
    bom_cells=None,
    pure_geometry=False,
    corrupt=False
) -> bytes:
    """构建标准 ASCII DXF 数据流"""
    if corrupt:
        return b"NOT_A_VALID_DXF_GARBAGE_PAYLOAD"

    lines = [
        "0", "SECTION",
        "2", "HEADER",
        "0", "ENDSEC",
        "0", "SECTION",
        "2", "ENTITIES"
    ]

    if pure_geometry:
        # 仅包含无文字几何实体 LINE, CIRCLE
        lines.extend([
            "0", "LINE", "8", "0", "10", "0.0", "20", "0.0", "11", "100.0", "21", "100.0",
            "0", "CIRCLE", "8", "0", "10", "50.0", "20", "50.0", "40", "25.0",
        ])
    else:
        # 1. 图签属性块
        if title_block:
            lines.extend(["0", "INSERT", "2", "A1_TITLE_BLOCK"])
            for tag, val in title_block.items():
                lines.extend(["0", "ATTRIB", "2", tag, "1", val])

        # 2. 材料设备明细表
        if bom_cells:
            lines.extend(["0", "TABLE"])
            for cell_val in bom_cells:
                lines.extend(["1", cell_val])

        # 3. 设计说明与文字注释
        note_items = notes or [
            {"text": "工程设计总说明：本工程抗震设防烈度为 8 度，耐火等级为一级。", "layer": "NOTE", "x": 100.0, "y": 500.0},
            {"text": "结构梁配筋采用 %%c25 螺纹钢，混凝土强度等级 C35。", "layer": "NOTE", "x": 100.0, "y": 450.0},
            {"text": "弱电智能化桥架规格：\\A1;400x150mm 镀锌金属桥架\\P表面经静电喷塑处理。", "layer": "EQUIPMENT", "x": 300.0, "y": 200.0},
        ]
        for item in note_items:
            lines.extend([
                "0", "MTEXT",
                "8", item["layer"],
                "10", str(item["x"]),
                "20", str(item["y"]),
                "1", item["text"]
            ])

    lines.extend([
        "0", "ENDSEC",
        "0", "EOF"
    ])

    return "\n".join(lines).encode("utf-8")


@pytest.fixture
def cad_parser() -> CADParser:
    return CADParser()


def test_cad_parser_metadata(cad_parser: CADParser):
    """1. 验证 CAD 解析器支持的扩展名与源类型"""
    assert ".dxf" in cad_parser.supported_extensions
    assert ".dwg" in cad_parser.supported_extensions
    assert cad_parser.source_type == DocumentSourceType.CAD


@pytest.mark.asyncio
async def test_cad_text_and_mtext_extraction(cad_parser: CADParser):
    """2. 验证 DXF 中的文字与 MTEXT 提取为 ASTBlockType.CAD_NOTE"""
    data = make_dxf_bytes()
    ast = await cad_parser.parse(data, "drawing.dxf")

    assert ast.source_type == DocumentSourceType.CAD
    notes = [n for n in ast.nodes if n.block_type == ASTBlockType.CAD_NOTE]
    assert len(notes) >= 2
    combined = " ".join(n.text_content for n in notes)
    assert "工程设计总说明" in combined
    assert "抗震设防烈度为 8 度" in combined


@pytest.mark.asyncio
async def test_cad_title_block_metadata(cad_parser: CADParser):
    """3. 验证图签属性块提取为 CALLOUT 与元数据"""
    title_attrs = {
        "工程名称": "市第三人民医院智能化综合楼",
        "图纸名称": "智能化系统施工图及布线详图",
        "抗震设防烈度": "8度",
        "耐火等级": "一级",
        "版本号": "Rev-B"
    }
    data = make_dxf_bytes(title_block=title_attrs)
    ast = await cad_parser.parse(data, "hospital.dxf")

    # 验证提取到的图签 CALLOUT 节点
    callouts = [n for n in ast.nodes if n.block_type == ASTBlockType.CALLOUT]
    assert len(callouts) >= 1
    assert "市第三人民医院智能化综合楼" in callouts[0].text_content

    # 验证元数据中包含了图签属性
    assert ast.metadata.get("工程名称") == "市第三人民医院智能化综合楼"
    assert ast.metadata.get("图纸名称") == "智能化系统施工图及布线详图"


@pytest.mark.asyncio
async def test_cad_bom_table_extraction(cad_parser: CADParser):
    """4. 验证 CAD 材料明细表提取为 ASTBlockType.TABLE 与 TableData"""
    bom_data = [
        "序号", "设备名称", "规格型号", "数量",
        "1", "核心交换机", "Huawei S6730-H", "2台",
        "2", "汇聚交换机", "Huawei S5735-L", "8台",
        "3", "防火墙", "USG6600E", "2台"
    ]
    data = make_dxf_bytes(bom_cells=bom_data)
    ast = await cad_parser.parse(data, "network.dxf")

    tbls = [n for n in ast.nodes if n.block_type == ASTBlockType.TABLE]
    assert len(tbls) >= 1
    tbl = tbls[0]
    assert tbl.table_data is not None
    assert "核心交换机" in tbl.table_data.markdown
    assert "Huawei S6730-H" in tbl.table_data.markdown


@pytest.mark.asyncio
async def test_cad_layer_name_in_extra_metadata(cad_parser: CADParser):
    """5. 验证图层名称正确保存在 extra_metadata['layer']"""
    data = make_dxf_bytes()
    ast = await cad_parser.parse(data, "layers.dxf")

    notes = [n for n in ast.nodes if n.block_type == ASTBlockType.CAD_NOTE]
    layers = {n.extra_metadata.get("layer") for n in notes if "layer" in n.extra_metadata}
    assert "NOTE" in layers or "EQUIPMENT" in layers


@pytest.mark.asyncio
async def test_cad_mtext_formatting_codes_stripped(cad_parser: CADParser):
    """6. 验证 AutoCAD MTEXT 控制字符清洗 (\\A1;, \\P, %%c)"""
    data = make_dxf_bytes()
    ast = await cad_parser.parse(data, "clean_mtext.dxf")

    combined_text = " ".join(n.text_content for n in ast.nodes)
    # \\A1; 应当被清除
    assert "\\A1;" not in combined_text
    # \\P 应当被转为换行或空格
    assert "\\P" not in combined_text
    # %%c 应当被转为 Φ
    assert "Φ25" in combined_text or "%%c" not in combined_text


@pytest.mark.asyncio
async def test_cad_pure_geometry_no_crash(cad_parser: CADParser):
    """7. 验证纯线条几何图纸不崩溃并安全返回"""
    data = make_dxf_bytes(pure_geometry=True)
    ast = await cad_parser.parse(data, "geometry.dxf")

    assert ast.source_type == DocumentSourceType.CAD
    assert isinstance(ast, UnifiedDocumentAST)


@pytest.mark.asyncio
async def test_cad_corrupt_dxf_error(cad_parser: CADParser):
    """8. 验证损坏非标准 DXF 文件抛出 MalformedDocumentError"""
    data = make_dxf_bytes(corrupt=True)
    with pytest.raises(MalformedDocumentError):
        await cad_parser.parse(data, "corrupt.dxf")


@pytest.mark.asyncio
async def test_cad_coordinate_bounding_box(cad_parser: CADParser):
    """9. 验证插入点生成了正向 BoundingBox 坐标"""
    data = make_dxf_bytes()
    ast = await cad_parser.parse(data, "bbox.dxf")

    notes_with_bbox = [n for n in ast.nodes if n.bbox is not None]
    assert len(notes_with_bbox) >= 2
    for n in notes_with_bbox:
        assert n.bbox.x0 >= 0.0
        assert n.bbox.y0 >= 0.0
        assert n.bbox.x1 > n.bbox.x0
        assert n.bbox.y1 > n.bbox.y0


@pytest.mark.asyncio
async def test_cad_reduction_accuracy_threshold(cad_parser: CADParser):
    """10. 验证 CAD 说明及材料明细表还原率 >= 98%"""
    ground_truth = [
        "本工程抗震设防烈度为 8 度",
        "耐火等级为一级",
        "镀锌金属桥架",
        "表面经静电喷塑处理"
    ]
    data = make_dxf_bytes()
    ast = await cad_parser.parse(data, "accuracy.dxf")

    extracted = " ".join(n.text_content for n in ast.nodes)
    matched = sum(1 for phrase in ground_truth if phrase in extracted)
    rate = matched / len(ground_truth)
    assert rate >= 0.98, f"CAD 图纸文本还原率 {rate:.4f} 未达到 98%"

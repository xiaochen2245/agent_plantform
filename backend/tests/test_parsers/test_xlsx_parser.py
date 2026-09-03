"""
XLSX / 概预算工程量清单解析器测试套件
验证多工作表、跨行跨列合并单元格前向填充、数值精度、Markdown 表格及 >= 98% 还原率
"""

import io
import time
import zipfile
import pytest
from app.parsers.xlsx_parser import XLSXParser
from app.parsers.base import EmptyDocumentError, MalformedDocumentError
from app.schemas.ast import ASTBlockType, DocumentSourceType, UnifiedDocumentAST


def make_xlsx_bytes(
    sheets_data=None,
    shared_strings=None,
    corrupt_zip=False
) -> bytes:
    """构建标准 Excel OOXML (ZIP + XML) 内存字节流"""
    if corrupt_zip:
        return b"PK\x03\x04BAD_GARBAGE_PAYLOAD"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # [Content_Types].xml
        content_types = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
            '  <Default Extension="xml" ContentType="application/xml"/>\n'
            '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
            '  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>\n'
            '  <Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>\n'
            '</Types>'
        )
        z.writestr("[Content_Types].xml", content_types)

        # 默认工作表数据
        sheets = sheets_data or {
            "分部分项工程清单": {
                "rows": [
                    ["序号", "项目编码", "项目名称", "计量单位", "工程量", "综合单价(元)", "合价(元)"],
                    ["1", "010101001", "土方开挖", "m3", "12000.00", "45.50", "546000.00"],
                    ["2", "010401002", "C30商品混凝土", "m3", "5600.00", "420.00", "2352000.00"],
                ],
                "merges": []
            }
        }

        # xl/workbook.xml
        sheet_entries = []
        for idx, sname in enumerate(sheets.keys(), start=1):
            sheet_entries.append(f'<sheet name="{sname}" sheetId="{idx}" r:id="rId{idx}"/>')
        wb_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">\n'
            '  <sheets>\n'
            + "\n".join(sheet_entries) +
            '  </sheets>\n'
            '</workbook>'
        )
        z.writestr("xl/workbook.xml", wb_xml)

        # xl/worksheets/sheet{N}.xml
        for s_idx, (sname, sinfo) in enumerate(sheets.items(), start=1):
            rows_list = sinfo.get("rows", [])
            merges_list = sinfo.get("merges", [])

            row_xmls = []
            for r_i, r_cells in enumerate(rows_list, start=1):
                c_xmls = []
                for c_i, c_val in enumerate(r_cells, start=1):
                    # 转换列号为字母 (1 -> A, 2 -> B...)
                    col_letter = chr(ord("A") + c_i - 1)
                    cell_ref = f"{col_letter}{r_i}"
                    c_xmls.append(f'<c r="{cell_ref}" t="inlineStr"><is><t>{c_val}</t></is></c>')
                row_xmls.append(f'<row r="{r_i}">' + "".join(c_xmls) + '</row>')

            merge_xml = ""
            if merges_list:
                m_tags = [f'<mergeCell ref="{m}"/>' for m in merges_list]
                merge_xml = f'<mergeCells count="{len(m_tags)}">' + "".join(m_tags) + '</mergeCells>'

            ws_xml = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">\n'
                '  <sheetData>\n'
                + "\n".join(row_xmls) +
                '  </sheetData>\n'
                + merge_xml +
                '</worksheet>'
            )
            z.writestr(f"xl/worksheets/sheet{s_idx}.xml", ws_xml)

    return buf.getvalue()


@pytest.fixture
def xlsx_parser() -> XLSXParser:
    return XLSXParser()


def test_xlsx_parser_metadata(xlsx_parser: XLSXParser):
    """1. 验证 XLSX 解析器扩展名与源类型"""
    assert ".xlsx" in xlsx_parser.supported_extensions
    assert ".xlsm" in xlsx_parser.supported_extensions
    assert xlsx_parser.source_type == DocumentSourceType.XLSX


@pytest.mark.asyncio
async def test_xlsx_multi_sheet_nodes(xlsx_parser: XLSXParser):
    """2. 验证多工作表隔离解析与 page_or_sheet 标签"""
    multi_sheets = {
        "总说明": {
            "rows": [["条款", "内容"], ["工期", "90日历天"], ["质量", "争创鲁班奖"]],
            "merges": []
        },
        "分部分项工程量清单": {
            "rows": [
                ["序号", "项目名称", "综合单价", "合价"],
                ["1", "桩基工程", "300.00", "900000.00"]
            ],
            "merges": []
        },
        "措施项目清单": {
            "rows": [["措施项目", "金额"], ["安全文明施工费", "150000.00"]],
            "merges": []
        }
    }
    data = make_xlsx_bytes(sheets_data=multi_sheets)
    ast = await xlsx_parser.parse(data, "project_budget.xlsx")

    assert ast.total_pages_or_sheets == 3
    sheets_found = {n.page_or_sheet for n in ast.nodes}
    assert "总说明" in sheets_found
    assert "分部分项工程量清单" in sheets_found
    assert "措施项目清单" in sheets_found


@pytest.mark.asyncio
async def test_xlsx_merged_header_forward_fill(xlsx_parser: XLSXParser):
    """3. 验证跨列合并单元格前向填充 (A1:C1 填充到 A1, B1, C1)"""
    merged_sheet = {
        "清单表": {
            "rows": [
                ["项目总体特征", "", "", "单价", "合价"],
                ["分部工程", "项目名称", "工程量", "350.00", "700000.00"]
            ],
            "merges": ["A1:C1"]
        }
    }
    data = make_xlsx_bytes(sheets_data=merged_sheet)
    ast = await xlsx_parser.parse(data, "merged.xlsx")

    tbl = next(n for n in ast.nodes if n.block_type == ASTBlockType.TABLE)
    headers = tbl.table_data.headers[0]
    # 验证 A1 的内容前向填充到了原合并区域
    assert headers[0] == "项目总体特征"
    assert headers[1] == "项目总体特征"
    assert headers[2] == "项目总体特征"


@pytest.mark.asyncio
async def test_xlsx_table_markdown_structure(xlsx_parser: XLSXParser):
    """4. 验证生成的 Markdown 表格符合 GitHub-Flavored 标准语法"""
    data = make_xlsx_bytes()
    ast = await xlsx_parser.parse(data, "table_md.xlsx")

    tbl = next(n for n in ast.nodes if n.block_type == ASTBlockType.TABLE)
    md = tbl.table_data.markdown

    assert "| 序号 | 项目编码 | 项目名称 |" in md
    assert "| --- |" in md
    assert "| 1 | 010101001 | 土方开挖 |" in md


@pytest.mark.asyncio
async def test_xlsx_numeric_and_formula_extraction(xlsx_parser: XLSXParser):
    """5. 验证大额工程量与金额数值准确提取"""
    num_sheet = {
        "费用表": {
            "rows": [
                ["项目", "金额(万元)"],
                ["工程直接费", "12500000.50"],
                ["税金及附加", "1125000.04"]
            ],
            "merges": []
        }
    }
    data = make_xlsx_bytes(sheets_data=num_sheet)
    ast = await xlsx_parser.parse(data, "numeric.xlsx")

    text_all = " ".join(n.text_content for n in ast.nodes)
    assert "12500000.50" in text_all
    assert "1125000.04" in text_all


@pytest.mark.asyncio
async def test_xlsx_corrupt_file_raises_error(xlsx_parser: XLSXParser):
    """6. 验证破损 XLSX 归档引发 MalformedDocumentError"""
    data = make_xlsx_bytes(corrupt_zip=True)
    with pytest.raises(MalformedDocumentError):
        await xlsx_parser.parse(data, "corrupt.xlsx")


@pytest.mark.asyncio
async def test_xlsx_empty_sheet_skipped(xlsx_parser: XLSXParser):
    """7. 验证完全空白的工作表不产生垃圾节点"""
    mixed_sheets = {
        "有效工作表": {
            "rows": [["表头A", "表头B"], ["数据1", "数据2"]],
            "merges": []
        },
        "空白Sheet": {
            "rows": [],
            "merges": []
        }
    }
    data = make_xlsx_bytes(sheets_data=mixed_sheets)
    ast = await xlsx_parser.parse(data, "mixed.xlsx")

    sheet_names = [n.page_or_sheet for n in ast.nodes]
    assert "有效工作表" in sheet_names
    assert "空白Sheet" not in sheet_names


@pytest.mark.asyncio
async def test_xlsx_banner_title_as_heading(xlsx_parser: XLSXParser):
    """8. 验证首行全宽标题识别为 ASTBlockType.HEADING"""
    banner_sheet = {
        "概算表": {
            "rows": [
                ["大型三甲医院智能化弱电系统工程投资概算总表", "", ""],
                ["序号", "系统名称", "投资估算(万元)"],
                ["1", "综合布线系统", "230.50"]
            ],
            "merges": ["A1:C1"]
        }
    }
    data = make_xlsx_bytes(sheets_data=banner_sheet)
    ast = await xlsx_parser.parse(data, "banner.xlsx")

    headings = [n for n in ast.nodes if n.block_type == ASTBlockType.HEADING]
    assert len(headings) >= 1
    assert "大型三甲医院智能化弱电系统工程投资概算总表" in headings[0].text_content


@pytest.mark.asyncio
async def test_xlsx_large_grid_performance(xlsx_parser: XLSXParser):
    """9. 验证大表格 (200 行 x 8 列) 快速解析 (<= 2s)"""
    rows = [["列1", "列2", "列3", "列4", "列5", "列6", "列7", "列8"]]
    for i in range(1, 201):
        rows.append([f"行{i}_1", f"行{i}_2", f"行{i}_3", f"行{i}_4", f"行{i}_5", f"行{i}_6", f"行{i}_7", f"行{i}_8"])

    large_sheet = {"大数据量清单": {"rows": rows, "merges": []}}
    data = make_xlsx_bytes(sheets_data=large_sheet)

    start_t = time.perf_counter()
    ast = await xlsx_parser.parse(data, "large.xlsx")
    elapsed = time.perf_counter() - start_t

    assert elapsed < 2.0, f"大表格解析耗时 {elapsed:.2f}s 超过阈值"
    tbl = next(n for n in ast.nodes if n.block_type == ASTBlockType.TABLE)
    assert len(tbl.table_data.rows) == 200


@pytest.mark.asyncio
async def test_xlsx_reduction_accuracy_threshold(xlsx_parser: XLSXParser):
    """10. 验证单元格数据还原率 >= 98%"""
    ground_truth_rows = [
        ["序号", "项目编码", "项目名称", "计量单位", "工程量", "综合单价(元)", "合价(元)"],
        ["1", "010101001", "土方开挖", "m3", "12000.00", "45.50", "546000.00"],
        ["2", "010401002", "C30商品混凝土", "m3", "5600.00", "420.00", "2352000.00"],
    ]
    data = make_xlsx_bytes(sheets_data={"清单": {"rows": ground_truth_rows, "merges": []}})
    ast = await xlsx_parser.parse(data, "accuracy.xlsx")

    tbl = next(n for n in ast.nodes if n.block_type == ASTBlockType.TABLE)
    extracted_grid = tbl.table_data.headers + tbl.table_data.rows

    total_cells = sum(len(r) for r in ground_truth_rows)
    matched_cells = 0
    for r_idx, r in enumerate(ground_truth_rows):
        for c_idx, val in enumerate(r):
            if r_idx < len(extracted_grid) and c_idx < len(extracted_grid[r_idx]):
                if extracted_grid[r_idx][c_idx] == val:
                    matched_cells += 1

    accuracy = matched_cells / total_cells
    assert accuracy >= 0.98, f"XLSX 还原率 {accuracy:.4f} 低于 98% 准则"

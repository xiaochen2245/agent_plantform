"""
高并发压力与极限性能测试套件共享夹具 (conftest_stress.py)
提供合成千页大文档生成器、真值地标集合与性能监控夹具
"""

import io
import zipfile
from typing import Any, Dict, List
import pytest


def generate_synthetic_1000p_pdf(num_pages: int = 1000) -> bytes:
    """
    生成 1000 页合成 PDF 字节流
    包含标准 %PDF-1.7 头、目录大纲书签 (/Title (...))、双层文本块 (BT ... Tm ... Tj ET)
    """
    lines = ["%PDF-1.7"]
    for i in range(1, num_pages + 1):
        title = f"第{i}章 智能工程施工规范与质量标准第{i}分册"
        body = f"本工程第{i}分册严格执行国家施工质量验收标准，总日历天数为 720 天，造价控制在 48500 万元。"
        lines.append(f"/Title ({title})")
        lines.append(f"BT 1 0 0 1 50.0 750.0 Tm ({title}) Tj ET")
        lines.append(f"BT 1 0 0 1 50.0 700.0 Tm ({body}) Tj ET")
    lines.append("%%EOF")
    return "\n".join(lines).encode("utf-8")


def generate_synthetic_1000p_docx(num_chapters: int = 500) -> bytes:
    """
    生成 500 章节大型 DOCX 压缩归档字节流 (等效 1000+ 页体量)
    包含段落、多级标题与结构化表格
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        ct_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
            '  <Default Extension="xml" ContentType="application/xml"/>\n'
            '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
            '  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>\n'
            '</Types>'
        )
        z.writestr("[Content_Types].xml", ct_xml)

        xml_parts = [
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">\n'
            '<w:body>\n'
        ]
        for i in range(1, num_chapters + 1):
            h_text = f"第{i}章 智能化基础设施工程实施纲要"
            p_text = f"第{i}章第1节: 详细规范与参数说明，总工期严格控制在 360 个日历天，投资预算为 5000.00 万元。"
            xml_parts.append(
                f'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>{h_text}</w:t></w:r></w:p>\n'
            )
            xml_parts.append(
                f'<w:p><w:r><w:t>{p_text}</w:t></w:r></w:p>\n'
            )
            # 每 50 章插入一个表格
            if i % 50 == 0:
                xml_parts.append(
                    '<w:tbl>\n'
                    '  <w:tr>\n'
                    '    <w:tc><w:r><w:t>序号</w:t></w:r></w:tc>\n'
                    '    <w:tc><w:r><w:t>参数项</w:t></w:r></w:tc>\n'
                    '    <w:tc><w:r><w:t>要求值</w:t></w:r></w:tc>\n'
                    '  </w:tr>\n'
                    f'  <w:tr>\n'
                    f'    <w:tc><w:r><w:t>{i}</w:t></w:r></w:tc>\n'
                    '    <w:tc><w:r><w:t>冷机能效比 COP</w:t></w:r></w:tc>\n'
                    '    <w:tc><w:r><w:t>5.4</w:t></w:r></w:tc>\n'
                    '  </w:tr>\n'
                    '</w:tbl>\n'
                )
        xml_parts.append('</w:body>\n</w:document>')
        z.writestr("word/document.xml", "".join(xml_parts))
    return buf.getvalue()


def generate_synthetic_1000r_xlsx(num_rows: int = 1000) -> bytes:
    """
    生成 1000 行工程量清单 XLSX 压缩归档字节流
    包含工程编码、项目名称、计量单位、工程量、综合单价、合价
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        content_types = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
            '  <Default Extension="xml" ContentType="application/xml"/>\n'
            '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
            '  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>\n'
            '  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>\n'
            '</Types>'
        )
        z.writestr("[Content_Types].xml", content_types)

        wb_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">\n'
            '  <sheets>\n'
            '    <sheet name="分部分项工程量清单表" sheetId="1" r:id="rId1"/>\n'
            '  </sheets>\n'
            '</workbook>'
        )
        z.writestr("xl/workbook.xml", wb_xml)

        sheet_lines = [
            '<?xml version="1.0" encoding="UTF-8"?>\n',
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">\n',
            '  <sheetData>\n',
            '    <row r="1">\n',
            '      <c r="A1" t="inlineStr"><is><t>序号</t></is></c>\n',
            '      <c r="B1" t="inlineStr"><is><t>项目编码</t></is></c>\n',
            '      <c r="C1" t="inlineStr"><is><t>项目名称</t></is></c>\n',
            '      <c r="D1" t="inlineStr"><is><t>计量单位</t></is></c>\n',
            '      <c r="E1" t="inlineStr"><is><t>工程量</t></is></c>\n',
            '      <c r="F1" t="inlineStr"><is><t>综合单价</t></is></c>\n',
            '      <c r="G1" t="inlineStr"><is><t>合价</t></is></c>\n',
            '    </row>\n',
        ]
        for r in range(2, num_rows + 2):
            idx = r - 1
            sheet_lines.append(
                f'    <row r="{r}">\n'
                f'      <c r="A{r}" t="inlineStr"><is><t>{idx}</t></is></c>\n'
                f'      <c r="B{r}" t="inlineStr"><is><t>0101{idx:05d}</t></is></c>\n'
                f'      <c r="C{r}" t="inlineStr"><is><t>清单工程子目_{idx}</t></is></c>\n'
                f'      <c r="D{r}" t="inlineStr"><is><t>m2</t></is></c>\n'
                f'      <c r="E{r}" t="inlineStr"><is><t>100.00</t></is></c>\n'
                f'      <c r="F{r}" t="inlineStr"><is><t>250.00</t></is></c>\n'
                f'      <c r="G{r}" t="inlineStr"><is><t>25000.00</t></is></c>\n'
                f'    </row>\n'
            )
        sheet_lines.append('  </sheetData>\n</worksheet>')
        z.writestr("xl/worksheets/sheet1.xml", "".join(sheet_lines))
    return buf.getvalue()


def generate_ground_truth_spec() -> Dict[str, Any]:
    """
    提供真值地标集合，用于 AST 结构与语义还原率基准度量:
    涵盖 100 个各级标题、200 个正文段落、20 个结构化表格、10 个 CALLOUT 警示声明。
    总地标数 = 330 个。
    """
    headings = [f"第{i}章 智能系统建设设计说明" for i in range(1, 101)]
    paragraphs = [f"第{i}节 工程施工技术保障与质量检验控制标准细化条款" for i in range(1, 201)]
    tables = [f"设备参数表_{i}" for i in range(1, 21)]
    callouts = [f"【不可偏离/废标条款_{i}】不满足资质要求的投标文件将作废标处理" for i in range(1, 11)]
    return {
        "headings": headings,
        "paragraphs": paragraphs,
        "tables": tables,
        "callouts": callouts,
        "total_count": len(headings) + len(paragraphs) + len(tables) + len(callouts),
    }


@pytest.fixture(scope="session")
def synthetic_1000p_pdf_bytes() -> bytes:
    return generate_synthetic_1000p_pdf(1000)


@pytest.fixture(scope="session")
def synthetic_1000p_docx_bytes() -> bytes:
    return generate_synthetic_1000p_docx(500)


@pytest.fixture(scope="session")
def synthetic_1000r_xlsx_bytes() -> bytes:
    return generate_synthetic_1000r_xlsx(1000)


@pytest.fixture(scope="session")
def ground_truth_spec() -> Dict[str, Any]:
    return generate_ground_truth_spec()

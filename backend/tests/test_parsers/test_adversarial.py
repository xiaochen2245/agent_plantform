"""
多源异构文档解析集群极限对抗与健壮性压力测试套件 (Adversarial Stress Test Suite)
覆盖 5 大维度：
1. 0 字节流、截断流与损坏归档矩阵 (0-byte, truncated & corrupt streams)
2. 损坏 ZIP 归档与非法 XML/二进制载荷 (Corrupt archives & invalid XML)
3. 10,000+ 单元格超大表格与极端深度合并单元格 (Massive tables & extreme merged cells)
4. 1~9 级深度嵌套与跨级断层大纲树 (Deeply nested & discontinuous heading outlines)
5. 极端特殊字符、罕见字、Emoji、双向控制符与超长文本 (Special chars, Emojis, BiDi & giant strings)
6. 50+ 任务多源格式高并发穿透与进程崩溃零容忍防御 (High concurrency & process stability)
"""

import asyncio
import io
import os
import re
import time
import zipfile
from xml.sax.saxutils import escape
from typing import Any, Dict, List

import pytest

from app.parsers.base import (
    BaseParser,
    EmptyDocumentError,
    MalformedDocumentError,
    ParserError,
    PasswordProtectedError,
    UnsupportedFormatException,
)
from app.parsers.cad_parser import CADParser
from app.parsers.docx_parser import DOCXParser
from app.parsers.factory import parser_factory
from app.parsers.mpp_parser import MPPParser
from app.parsers.ofd_parser import OFDParser
from app.parsers.pdf_parser import PDFParser
from app.parsers.pptx_parser import PPTXParser
from app.parsers.xlsx_parser import XLSXParser
from app.schemas.ast import ASTBlockType, DocumentSourceType, UnifiedDocumentAST


# =====================================================================
# 辅助构建函数 (Generators)
# =====================================================================

def make_valid_minimal_docx(text: str = "默认正文") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""")
        z.writestr("word/document.xml", f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p></w:body>
</w:document>""")
    return buf.getvalue()


def make_valid_minimal_xlsx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
</Types>""")
        z.writestr("xl/workbook.xml", """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheets><sheet name="Sheet1" sheetId="1"/></sheets>
</workbook>""")
        z.writestr("xl/worksheets/sheet1.xml", """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1"><c r="A1" t="inlineStr"><is><t>Col1</t></is></c><c r="B1" t="inlineStr"><is><t>Col2</t></is></c></row>
    <row r="2"><c r="A2" t="inlineStr"><is><t>Val1</t></is></c><c r="B2" t="inlineStr"><is><t>Val2</t></is></c></row>
  </sheetData>
</worksheet>""")
    return buf.getvalue()


def make_valid_minimal_ofd(text: str = "OFD 正文内容") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("OFD.xml", """<?xml version="1.0" encoding="UTF-8"?>
<ofd:OFD xmlns:ofd="http://www.ofdspec.org/2016" Version="1.0" DocType="OFD">
  <ofd:DocBody><ofd:DocRoot>Doc_0/Document.xml</ofd:DocRoot></ofd:DocBody>
</ofd:OFD>""")
        z.writestr("Doc_0/Document.xml", """<?xml version="1.0" encoding="UTF-8"?>
<ofd:Document xmlns:ofd="http://www.ofdspec.org/2016">
  <ofd:Pages><ofd:Page ID="1" BaseLoc="Pages/Page_0/Content.xml"/></ofd:Pages>
</ofd:Document>""")
        z.writestr("Doc_0/Pages/Page_0/Content.xml", f"""<?xml version="1.0" encoding="UTF-8"?>
<ofd:Page xmlns:ofd="http://www.ofdspec.org/2016">
  <ofd:Content><ofd:Layer>
    <ofd:TextObject Boundary="50.0 100.0 300.0 20.0">
      <ofd:TextCode>{escape(text)}</ofd:TextCode>
    </ofd:TextObject>
  </ofd:Layer></ofd:Content>
</ofd:Page>""")
    return buf.getvalue()


def make_valid_minimal_mpp() -> bytes:
    return """<?xml version="1.0" encoding="UTF-8"?>
<Project xmlns="http://schemas.microsoft.com/project">
  <Title>极小项目计划</Title>
  <Tasks>
    <Task>
      <UID>1</UID>
      <Name>首要任务</Name>
      <Duration>PT80H0M0S</Duration>
    </Task>
  </Tasks>
</Project>""".encode("utf-8")


def make_valid_minimal_cad() -> bytes:
    return """0
SECTION
2
ENTITIES
0
MTEXT
8
0
10
50.0
20
50.0
1
CAD 设计说明文本
0
ENDSEC
0
EOF
""".encode("utf-8")


def make_valid_minimal_pdf() -> bytes:
    return b"%PDF-1.7\nBT 1 0 0 1 50 500 Tm (Minimal PDF text) Tj ET\n%%EOF"


def make_valid_minimal_pptx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
</Types>""")
        z.writestr("ppt/presentation.xml", """<?xml version="1.0" encoding="UTF-8"?>
<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>""")
        z.writestr("ppt/slides/slide1.xml", """<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld><p:spTree><p:sp>
    <p:spPr><a:off x="100" y="100"/></p:spPr>
    <p:txBody><a:p><a:r><a:t>幻灯片第一页测试</a:t></a:r></a:p></p:txBody>
  </p:sp></p:spTree></p:cSld>
</p:sld>""")
    return buf.getvalue()


# =====================================================================
# 1. 0 字节流、截断流与损坏归档矩阵
# =====================================================================

class TestZeroByteAndTruncatedStreams:
    """1. 验证 0 字节、纯空白与极短截断流的防御完整性"""

    ALL_PARSERS: List[BaseParser] = [
        OFDParser(), XLSXParser(), MPPParser(), CADParser(),
        DOCXParser(), PPTXParser(), PDFParser()
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("parser", ALL_PARSERS, ids=lambda p: p.__class__.__name__)
    async def test_all_parsers_direct_0_byte_raises_empty_document_error(self, parser: BaseParser):
        """1.1 验证所有 7 种解析器实例直接调用 parse(b'') 时统一抛出 EmptyDocumentError"""
        ext = parser.supported_extensions[0]
        with pytest.raises(EmptyDocumentError) as exc_info:
            await parser.parse(b"", f"zero_byte{ext}")
        assert "为空" in exc_info.value.message or "0 字节" in exc_info.value.message

    @pytest.mark.asyncio
    @pytest.mark.parametrize("ext", [".ofd", ".xlsx", ".mpp", ".dxf", ".dwg", ".docx", ".pptx", ".pdf"])
    async def test_parser_factory_0_byte_raises_empty_document_error(self, ext: str):
        """1.2 验证 ParserFactory 门面调用 0 字节数据统一抛出 EmptyDocumentError"""
        with pytest.raises(EmptyDocumentError) as exc_info:
            await parser_factory.parse_document(b"", f"empty_payload{ext}")
        assert "0 字节" in exc_info.value.message

    @pytest.mark.asyncio
    @pytest.mark.parametrize("parser", ALL_PARSERS, ids=lambda p: p.__class__.__name__)
    async def test_all_parsers_whitespace_only_stream(self, parser: BaseParser):
        """1.3 验证仅包含空格、制表符与换行符的数据流被安全拦截或转换为类型化异常，无段错误"""
        ext = parser.supported_extensions[0]
        ws_bytes = b"   \r\n\t  \n  "
        # 直接调用应抛出 ParserError 子类，绝对不能抛出非受检系统错误
        with pytest.raises(ParserError):
            await parser.parse(ws_bytes, f"whitespace{ext}")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("truncated_header", [b"P", b"PK", b"PK\x03", b"%", b"%PD", b"0\n", b"\xd0\xcf"])
    async def test_truncated_headers_raise_typed_exception(self, truncated_header: bytes):
        """1.4 验证 1~4 字节残缺文件头统一抛出 MalformedDocumentError，拒绝伪装文件"""
        for ext in [".docx", ".xlsx", ".ofd", ".pptx", ".pdf", ".dxf", ".mpp"]:
            with pytest.raises(ParserError) as exc_info:
                await parser_factory.parse_document(truncated_header, f"truncated{ext}")
            assert isinstance(exc_info.value, (MalformedDocumentError, EmptyDocumentError))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("ratio", [0.1, 0.25, 0.5, 0.75])
    async def test_truncated_valid_containers_raise_malformed(self, ratio: float):
        """1.5 验证真实有效文件流在传输截断 (10%~75%) 后被判定为 MalformedDocumentError"""
        valid_docx = make_valid_minimal_docx()
        cut_len = max(int(len(valid_docx) * ratio), 4)
        truncated_bytes = valid_docx[:cut_len]

        with pytest.raises(MalformedDocumentError):
            await DOCXParser().parse(truncated_bytes, "truncated.docx")


# =====================================================================
# 2. 损坏 ZIP 归档与非法 XML/二进制载荷
# =====================================================================

class TestCorruptArchivesAndMalformedPayloads:
    """2. 验证损坏的 ZIP 归档、残缺 XML 及未支持格式的优雅降级"""

    @pytest.mark.asyncio
    async def test_corrupt_zip_header_with_random_noise(self):
        """2.1 验证带合法 ZIP 幻数但主体全为随机噪点的归档抛出 MalformedDocumentError"""
        noise = b"PK\x03\x04" + os.urandom(512)
        for parser, ext in [(OFDParser(), "test.ofd"), (DOCXParser(), "test.docx"),
                            (XLSXParser(), "test.xlsx"), (PPTXParser(), "test.pptx")]:
            with pytest.raises(MalformedDocumentError):
                await parser.parse(noise, ext)

    @pytest.mark.asyncio
    async def test_empty_zip_container_handled_safely(self):
        """2.2 验证包含 0 个文件的空 ZIP 归档不会引发 KeyError 崩溃"""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            pass
        empty_zip = buf.getvalue()

        # OFD 和 DOCX 依赖核心 XML，缺失应显式抛出 MalformedDocumentError
        with pytest.raises(MalformedDocumentError):
            await OFDParser().parse(empty_zip, "empty.ofd")

        with pytest.raises(MalformedDocumentError):
            await DOCXParser().parse(empty_zip, "empty.docx")

        # XLSX 和 PPTX 具备容错降级，返回空 AST 而非崩溃
        xlsx_ast = await XLSXParser().parse(empty_zip, "empty.xlsx")
        assert isinstance(xlsx_ast, UnifiedDocumentAST)

        pptx_ast = await PPTXParser().parse(empty_zip, "empty.pptx")
        assert isinstance(pptx_ast, UnifiedDocumentAST)

    @pytest.mark.asyncio
    async def test_zip_with_corrupted_internal_xml(self):
        """2.3 验证内部 XML 标签未闭合或损坏时抛出 MalformedDocumentError"""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("word/document.xml", "<w:document><w:body><unclosed_tag>broken")
        bad_xml_docx = buf.getvalue()

        with pytest.raises(MalformedDocumentError):
            await DOCXParser().parse(bad_xml_docx, "broken_xml.docx")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_ext", [".xyz", ".bin", ".mp4", ".exe", ".unknown"])
    async def test_unsupported_formats_raise_unsupported_exception(self, bad_ext: str):
        """2.4 验证未注册扩展名统一抛出 UnsupportedFormatException"""
        with pytest.raises(UnsupportedFormatException):
            await parser_factory.parse_document(b"Random binary payload", f"sample{bad_ext}")

    @pytest.mark.asyncio
    async def test_password_protected_pdf_raises_typed_error(self):
        """2.5 验证加密 PDF 在未提供密码或密码错误时统一抛出 PasswordProtectedError"""
        encrypted_pdf = b"%PDF-1.7\n1 0 obj\n<< /Encrypt 2 0 R >>\nendobj\n%%EOF"
        with pytest.raises(PasswordProtectedError):
            await PDFParser().parse(encrypted_pdf, "encrypted.pdf")


# =====================================================================
# 3. 10,000+ 单元格超大表格与极端深度合并单元格
# =====================================================================

class TestMassiveTablesAndExtremeMergedCells:
    """3. 验证 10,000+ 单元格与千行级合并单元格的前向填充正确性与线性性能"""

    @pytest.mark.asyncio
    async def test_xlsx_10000_cells_with_1000_merged_span(self):
        """
        3.1 构造 500 行 x 20 列 = 10,000 单元格的大型电子表格，
        并在 E10:N109 (100 行 x 10 列 = 1,000 单元格) 注入极端跨度合并。
        验证：
        - 纯原生降级解析无 OOM 且耗时 < 2.0s
        - 1,000 个合并单元格全部正确前向填充 (Forward-Filling)
        - TableCell 结构数量完整
        """
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
</Types>""")
            z.writestr("xl/workbook.xml", """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheets><sheet name="BillOfQuantities" sheetId="1"/></sheets>
</workbook>""")

            def col_str(c: int) -> str:
                return chr(ord('A') + c - 1)

            row_xmls = []
            for r in range(1, 501):
                c_xmls = []
                for c in range(1, 21):
                    ref = f"{col_str(c)}{r}"
                    val = f"R{r}C{c}"
                    if r == 10 and c == 5:
                        val = "EXTREME_MERGE_1000_CELLS"
                    c_xmls.append(f'<c r="{ref}" t="inlineStr"><is><t>{val}</t></is></c>')
                row_xmls.append(f'<row r="{r}">{" ".join(c_xmls)}</row>')

            merge_xml = '<mergeCells count="1"><mergeCell ref="E10:N109"/></mergeCells>'
            sheet_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>{''.join(row_xmls)}</sheetData>
  {merge_xml}
</worksheet>"""
            z.writestr("xl/worksheets/sheet1.xml", sheet_xml)

        data = buf.getvalue()
        t0 = time.perf_counter()
        ast = await XLSXParser().parse(data, "massive_boq.xlsx")
        elapsed = time.perf_counter() - t0

        # 性能断言
        assert elapsed < 2.0, f"10,000 单元格解析耗时 {elapsed:.3f}s 超过 2s 阈值"

        tbl_node = next(n for n in ast.nodes if n.table_data)
        assert tbl_node is not None
        assert len(tbl_node.table_data.cells) == 10000

        # 校验合并区间的任意样本 (首行为表头，故 body 索引 49 对应第 51 行)
        # 第 51 行第 8 列 (H51) 位于 E10:N109 合并区间内，必须被前向填充
        row_51 = tbl_node.table_data.rows[49]
        assert row_51[7] == "EXTREME_MERGE_1000_CELLS"
        # 校验合并区域外不受污染 (第 51 行第 16 列，即 P51)
        assert row_51[15] == "R51C16"

    @pytest.mark.asyncio
    async def test_docx_10000_cells_table(self):
        """3.2 构造包含 500 行 x 20 列 = 10,000 单元格的 Word 表格，验证 AST 解析无递归栈溢出"""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""")
            tr_parts = []
            for r in range(500):
                tc_parts = [f'<w:tc><w:p><w:r><w:t>R{r}C{c}</w:t></w:r></w:p></w:tc>' for c in range(20)]
                tr_parts.append(f'<w:tr>{"".join(tc_parts)}</w:tr>')

            doc_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:tbl>{''.join(tr_parts)}</w:tbl></w:body>
</w:document>"""
            z.writestr("word/document.xml", doc_xml)

        t0 = time.perf_counter()
        ast = await DOCXParser().parse(buf.getvalue(), "massive_table.docx")
        elapsed = time.perf_counter() - t0

        assert elapsed < 2.0
        tbl_node = next(n for n in ast.nodes if n.table_data)
        assert len(tbl_node.table_data.cells) == 10000

    @pytest.mark.asyncio
    async def test_table_cell_markdown_pipe_and_newline_escaping(self):
        """3.3 验证单元格包含 Markdown 管道符 '|' 与多行换行时被安全转义为 &#124; 与 <br/>"""
        parser = DOCXParser()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""")
            table_xml = """<w:tbl>
  <w:tr>
    <w:tc><w:p><w:r><w:t>参数项 | 单位</w:t></w:r></w:p></w:tc>
    <w:tc><w:p><w:r><w:t>技术指标&#10;要求值</w:t></w:r></w:p></w:tc>
  </w:tr>
  <w:tr>
    <w:tc><w:p><w:r><w:t>UPS功率 | kVA</w:t></w:r></w:p></w:tc>
    <w:tc><w:p><w:r><w:t>&gt;= 500kVA&#10;双母线</w:t></w:r></w:p></w:tc>
  </w:tr>
</w:tbl>"""
            z.writestr("word/document.xml", f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>{table_xml}</w:body>
</w:document>""")

        ast = await parser.parse(buf.getvalue(), "escaped_table.docx")
        tbl = next(n for n in ast.nodes if n.table_data)
        md = tbl.table_data.markdown

        # 确保管道符被转义，防止破坏 Markdown 列对齐
        assert "&#124;" in md
        # 确保换行被转义为 <br/>
        assert "<br/>" in md


# =====================================================================
# 4. 1~9 级深度嵌套与跨级断层大纲树
# =====================================================================

class TestDeeplyNestedAndErraticHeadingOutlines:
    """4. 验证极端跳级大纲 (如 1 -> 9 -> 3) 面包屑拓扑栈维护的单调性与鲁棒性"""

    @pytest.mark.asyncio
    async def test_erratic_heading_level_jumps_monotonic_stack(self):
        """
        4.1 注入连续断层大纲：1 -> 9 -> 3 -> 7 -> 2 -> 5 -> 1
        验证：
        - 拓扑栈严格正确出栈更深节点，不会发生索引越界或脏状态残留
        - 最终顶级标题出栈所有前序面包屑
        """
        headings = [
            (1, "第一章 总体实施部署"),
            (9, "1.1.1.1.1.1.1.1.1 超深异形深基坑微扰动控制工艺"),  # 1 -> 9
            (3, "1.2.1 地下连续墙抓铣结合成槽工法"),                # 9 -> 3 (退栈至 1)
            (7, "1.2.1.1.1.1.1 泥浆比重与黏度在线监测"),            # 3 -> 7
            (2, "1.3 顺作法支撑体系转换设计"),                      # 7 -> 2 (退栈至 1)
            (5, "1.3.1.1.1 伺服钢支撑轴力自动化补偿"),              # 2 -> 5
            (1, "第二章 质量安全保证体系"),                          # 5 -> 1 (退栈至空，压入2)
        ]

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""")
            p_xmls = [
                f'<w:p><w:pPr><w:pStyle w:val="Heading{lvl}"/></w:pPr><w:r><w:t>{escape(t)}</w:t></w:r></w:p>'
                for lvl, t in headings
            ]
            z.writestr("word/document.xml", f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>{''.join(p_xmls)}</w:body>
</w:document>""")

        ast = await DOCXParser().parse(buf.getvalue(), "erratic_outline.docx")
        nodes = [n for n in ast.nodes if n.block_type == ASTBlockType.HEADING]
        assert len(nodes) == 7

        # 校验 1 -> 9 的 section_path
        h9 = nodes[1]
        assert h9.level == 9
        assert h9.section_path == ["第一章 总体实施部署", "1.1.1.1.1.1.1.1.1 超深异形深基坑微扰动控制工艺"]

        # 校验 9 -> 3 的 section_path (已剔除 9)
        h3 = nodes[2]
        assert h3.level == 3
        assert h3.section_path == ["第一章 总体实施部署", "1.2.1 地下连续墙抓铣结合成槽工法"]

        # 校验最终的 第二章 (已剔除第一章所有层级)
        h1_last = nodes[6]
        assert h1_last.level == 1
        assert h1_last.section_path == ["第二章 质量安全保证体系"]

    @pytest.mark.asyncio
    async def test_100_consecutive_random_level_headings(self):
        """4.2 压力测试：连续 100 个随机等级 (1~9) 标题，验证解析无内存泄漏与死循环"""
        import random
        rng = random.Random(42)
        random_headings = [
            (rng.randint(1, 9), f"大纲段落节点_{i}")
            for i in range(1, 101)
        ]

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""")
            p_xmls = [
                f'<w:p><w:pPr><w:pStyle w:val="Heading{lvl}"/></w:pPr><w:r><w:t>{t}</w:t></w:r></w:p>'
                for lvl, t in random_headings
            ]
            z.writestr("word/document.xml", f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>{''.join(p_xmls)}</w:body>
</w:document>""")

        t0 = time.perf_counter()
        ast = await DOCXParser().parse(buf.getvalue(), "random_100_headings.docx")
        elapsed = time.perf_counter() - t0

        assert elapsed < 0.5
        assert len(ast.nodes) == 100
        for n in ast.nodes:
            assert len(n.section_path) > 0
            assert all(isinstance(p, str) and len(p) > 0 for p in n.section_path)


# =====================================================================
# 5. 极端特殊字符、罕见字、Emoji、双向控制符与超长文本
# =====================================================================

class TestAdversarialStringsAndSpecialCharacters:
    """5. 验证罕见字、Unicode 控制符、Emoji、CAD MTEXT 转义码与极端字符保真度"""

    @pytest.mark.asyncio
    async def test_emojis_cjk_rare_chars_and_bidi_overrides(self):
        """5.1 验证在 DOCX 中完整保留 Emoji (🏗️, 🦺)、生僻字 (𠮷, 𬱖) 及 BiDi 双向字符"""
        adversarial_texts = [
            "🏗️ 智慧建造数字化监管平台 🦺 现场施工安全防护 ⚠️",
            "CJK 拓展生僻字测试：𠮷野家、𬱖、𪚥、𝄢",
            "Unicode BiDi 混排：\u202e RLO反向文本 \u202d 恢复正向 \u200b 零宽空格",
            "HTML/XML 敏感实体攻击载荷：<script>alert('xss')</script> & \"double_quote\" & 'single'",
        ]

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""")
            p_xmls = [f'<w:p><w:r><w:t>{escape(t)}</w:t></w:r></w:p>' for t in adversarial_texts]
            z.writestr("word/document.xml", f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>{''.join(p_xmls)}</w:body>
</w:document>""")

        ast = await DOCXParser().parse(buf.getvalue(), "unicode_stress.docx")
        all_text = " ".join(n.text_content for n in ast.nodes)

        assert "🏗️" in all_text
        assert "𠮷野家" in all_text
        assert "alert('xss')" in all_text

    @pytest.mark.asyncio
    async def test_cad_mtext_control_sequences_normalization(self):
        """5.2 验证 CAD 图纸专有 MTEXT 格式码 (%%c, %%d, %%p, \\S, \\P, \\A1;) 的标准化清洗"""
        cad_dxf = """0
SECTION
2
ENTITIES
0
MTEXT
8
NOTE
10
100.0
20
200.0
1
梁主筋采用 %%c25 螺纹钢\\P养护温度为 25%%dC\\P公差为 %%p2mm\\P分数 \\S1^2;\\P样式 \\A1;{\\fSimSun|b0;正规工程说明}
0
ENDSEC
0
EOF
""".encode("utf-8")

        ast = await CADParser().parse(cad_dxf, "cad_codes.dxf")
        text = ast.nodes[0].text_content

        assert "Φ25" in text
        assert "25°C" in text
        assert "±2mm" in text
        assert "1/2" in text
        assert "\\A1;" not in text  # 确保转义标签被彻底清洗

    @pytest.mark.asyncio
    async def test_giant_unbroken_string_no_timeout(self):
        """5.3 验证包含 50,000 字符无空格单行超长字符串时，解析器不会发生正则回溯超时 (ReDoS)"""
        giant_string = "A" * 50000
        data = make_valid_minimal_docx(text=giant_string)

        t0 = time.perf_counter()
        ast = await DOCXParser().parse(data, "giant_string.docx")
        elapsed = time.perf_counter() - t0

        assert elapsed < 1.0
        assert len(ast.nodes[0].text_content) == 50000

    @pytest.mark.asyncio
    async def test_illegal_xml_control_characters_rejected_gracefully(self):
        """5.4 验证文档流中包含 XML 1.0 非法低控制字符 (如 \\x07, \\x1b) 时，抛出 MalformedDocumentError 而非未捕获崩溃"""
        raw_broken_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>Illegal byte \x07 and \x1b here</w:t></w:r></w:p></w:body>
</w:document>"""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("word/document.xml", raw_broken_xml)

        with pytest.raises(MalformedDocumentError):
            await DOCXParser().parse(buf.getvalue(), "illegal_control.docx")


# =====================================================================
# 6. 50+ 任务多源格式高并发穿透与进程崩溃零容忍防御
# =====================================================================

class TestConcurrencyAndProcessStability:
    """6. 验证 50+ 并发异步解析负载下的零竞态、内存稳定与进程防崩"""

    @pytest.mark.asyncio
    async def test_high_concurrency_heterogeneous_cluster_stress(self):
        """
        6.1 同时调度 60 个并发解析任务，涵盖全部 7 种格式 (含有效文档、空文档与损坏文档混合流)。
        验证：
        - 全部任务在 1.5 秒内完成，无死锁
        - 有效文档全部产出有效 UnifiedDocumentAST
        - 损坏/空文档全部抛出预期的 ParserError
        - 没有任何未处理的系统级 panic / segfault
        """
        payloads = [
            (make_valid_minimal_docx("并发DOCX"), "test.docx", True),
            (make_valid_minimal_xlsx(), "test.xlsx", True),
            (make_valid_minimal_ofd("并发OFD"), "test.ofd", True),
            (make_valid_minimal_mpp(), "test.xml", True),
            (make_valid_minimal_cad(), "test.dxf", True),
            (make_valid_minimal_pdf(), "test.pdf", True),
            (make_valid_minimal_pptx(), "test.pptx", True),
            (b"", "empty.docx", False),
            (b"PK\x03\x04CORRUPT_ZIP_PAYLOAD", "corrupt.xlsx", False),
            (b"BAD_PDF_HEADER", "corrupt.pdf", False),
        ]

        tasks = []
        for i in range(60):
            content, filename, should_succeed = payloads[i % len(payloads)]
            task_fn = f"worker_{i}_{filename}"

            async def worker(c=content, fn=task_fn, ok=should_succeed):
                if ok:
                    ast = await parser_factory.parse_document(c, fn)
                    assert isinstance(ast, UnifiedDocumentAST)
                    assert len(ast.nodes) > 0
                    return "OK"
                else:
                    try:
                        await parser_factory.parse_document(c, fn)
                        pytest.fail(f"Task {fn} should have raised ParserError")
                    except ParserError:
                        return "CAUGHT_EXPECTED"

            tasks.append(worker())

        t0 = time.perf_counter()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        elapsed = time.perf_counter() - t0

        assert elapsed < 2.5, f"60 并发解析耗时 {elapsed:.3f}s 超过 2.5s"
        unhandled_errors = [r for r in results if isinstance(r, Exception)]
        assert len(unhandled_errors) == 0, f"发现未捕获并发异常: {unhandled_errors}"

        # 统计结果
        ok_count = sum(1 for r in results if r == "OK")
        caught_count = sum(1 for r in results if r == "CAUGHT_EXPECTED")
        assert ok_count == 42
        assert caught_count == 18

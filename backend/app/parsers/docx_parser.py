"""
Word 技术标书与合同方案 (DOCX / DOC) 解析器
基于 python-docx 与底层 body XML 元素序列化遍历，精准保留段落与表格交错顺序、
多级大纲标题树 (1.1, 1.1.1)、表格跨行跨列合并单元格与关键警示声明 (CALLOUT)。
"""

import io
import re
import uuid
import xml.etree.ElementTree as ET
import zipfile
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import docx
    from docx.document import Document
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table, _Cell
    from docx.text.paragraph import Paragraph
    HAS_PYTHON_DOCX = True
except ImportError:
    HAS_PYTHON_DOCX = False

from app.schemas.ast import (
    ASTBlockType,
    ASTNode,
    DocumentSourceType,
    TableCell,
    TableData,
    UnifiedDocumentAST,
)
from .base import BaseParser, EmptyDocumentError, MalformedDocumentError


class DOCXParser(BaseParser):
    """
    DOCX 标书大纲树与交错排版解析适配器。
    按 Word 底层 XML 顺序流交错提取段落与表格，防止大纲与关联表格脱节。
    """

    HEADING_STYLE_PATTERN = re.compile(r"^(?:Heading|标题)\s*(\d+)$", re.IGNORECASE)

    @property
    def supported_extensions(self) -> List[str]:
        return [".docx", ".doc"]

    @property
    def source_type(self) -> DocumentSourceType:
        return DocumentSourceType.DOCX

    async def parse(
        self,
        content: bytes,
        file_name: str,
        tenant_id: str = "default",
        document_id: Optional[str] = None,
        **kwargs: Any,
    ) -> UnifiedDocumentAST:
        """异步解析 DOCX 字节流"""
        doc_id = document_id or f"doc_{uuid.uuid4().hex[:12]}"

        if not content:
            raise EmptyDocumentError(f"DOCX 文件 '{file_name}' 内容为空", file_name=file_name)

        if HAS_PYTHON_DOCX:
            try:
                doc = docx.Document(io.BytesIO(content))
                return self._parse_with_python_docx(doc, file_name, tenant_id, doc_id)
            except Exception as e:
                # 尝试纯原生 XML 降级
                return self._parse_pure_xml_fallback(content, file_name, tenant_id, doc_id)
        else:
            return self._parse_pure_xml_fallback(content, file_name, tenant_id, doc_id)

    def _parse_with_python_docx(
        self,
        doc: Any,
        file_name: str,
        tenant_id: str,
        doc_id: str
    ) -> UnifiedDocumentAST:
        """基于 python-docx 严格按底层 body 元素真实顺序遍历"""
        nodes: List[ASTNode] = []
        heading_stack: List[Tuple[int, str]] = []

        # 核心关键：遍历 doc.element.body 中的直接子元素，确保段落与表格顺序不颠倒
        for element in doc.element.body:
            if isinstance(element, CT_P):
                p = Paragraph(element, doc)
                text = self.clean_text(p.text)
                if not text:
                    continue

                # 1. 检测是否为标题样式
                style_name = (p.style.name if p.style else "") or ""
                style_match = self.HEADING_STYLE_PATTERN.match(style_name)
                inferred_lvl = int(style_match.group(1)) if style_match else self.infer_heading_level(text)

                if inferred_lvl:
                    section_path = self.update_section_stack(heading_stack, inferred_lvl, text)
                    nodes.append(
                        ASTNode(
                            block_id=self.generate_block_id("docx_h"),
                            block_type=ASTBlockType.HEADING,
                            level=inferred_lvl,
                            section_path=section_path,
                            text_content=text,
                            page_or_sheet="1",
                            extra_metadata={"style_name": style_name}
                        )
                    )
                else:
                    # 检查是否为关键警示/废标条款
                    is_callout = bool(re.search(r"(废标条款|重要提示|特别说明|不可偏离|严正声明|强制性标准)", text))
                    b_type = ASTBlockType.CALLOUT if is_callout else ASTBlockType.PARAGRAPH
                    current_path = [item[1] for item in heading_stack]

                    nodes.append(
                        ASTNode(
                            block_id=self.generate_block_id("docx_callout" if is_callout else "docx_p"),
                            block_type=b_type,
                            section_path=current_path,
                            text_content=text,
                            page_or_sheet="1",
                            extra_metadata={"style_name": style_name}
                        )
                    )

            elif isinstance(element, CT_Tbl):
                tbl = Table(element, doc)
                table_node = self._parse_docx_table(tbl, heading_stack)
                if table_node:
                    nodes.append(table_node)

        if not nodes:
            nodes.append(
                ASTNode(
                    block_id=self.generate_block_id("docx_empty"),
                    block_type=ASTBlockType.PARAGRAPH,
                    text_content="[Word 文档未包含任何有效文字段落或表格]",
                    page_or_sheet="1"
                )
            )

        return UnifiedDocumentAST(
            document_id=doc_id,
            tenant_id=tenant_id,
            file_name=file_name,
            source_type=self.source_type,
            total_pages_or_sheets=1,
            nodes=nodes,
            metadata={
                "parser": "DOCXParser",
                "extracted_nodes_count": len(nodes),
            }
        )

    def _parse_docx_table(
        self,
        table: Any,
        heading_stack: List[Tuple[int, str]]
    ) -> Optional[ASTNode]:
        """解析 Word 表格并处理合并单元格"""
        if not table.rows:
            return None

        n_rows = len(table.rows)
        n_cols = max(len(r.cells) for r in table.rows) if n_rows > 0 else 0
        if n_cols == 0:
            return None

        # 探测合并单元格：Word 中合并的单元格共享同一个底层 XML _tc 元素
        seen_cells: Dict[int, Tuple[int, int]] = {}
        grid: List[List[str]] = [["" for _ in range(n_cols)] for _ in range(n_rows)]
        table_cells: List[TableCell] = []

        for r_idx, row in enumerate(table.rows):
            for c_idx, cell in enumerate(row.cells):
                cell_text = self.clean_text(cell.text)
                cell_elem_id = id(cell._tc)

                if cell_elem_id in seen_cells:
                    # 属于之前某个合并单元格的一部分，前向填充
                    origin_r, origin_c = seen_cells[cell_elem_id]
                    grid[r_idx][c_idx] = grid[origin_r][origin_c]
                else:
                    seen_cells[cell_elem_id] = (r_idx, c_idx)
                    grid[r_idx][c_idx] = cell_text
                    table_cells.append(
                        TableCell(
                            row=r_idx,
                            col=c_idx,
                            row_span=1,
                            col_span=1,
                            text=cell_text,
                            is_header=(r_idx == 0)
                        )
                    )

        # 提取表头与数据行
        headers = [grid[0]] if grid else []
        body = grid[1:] if len(grid) > 1 else []

        md = self.build_table_markdown(headers=headers, rows=body)
        current_path = [item[1] for item in heading_stack]

        return ASTNode(
            block_id=self.generate_block_id("docx_tbl"),
            block_type=ASTBlockType.TABLE,
            section_path=current_path,
            text_content=md,
            table_data=TableData(
                headers=headers,
                rows=body,
                cells=table_cells,
                markdown=md,
                summary=f"Word 提取表格，共 {len(body)} 行数据。"
            ),
            page_or_sheet="1"
        )

    def _parse_pure_xml_fallback(
        self,
        content: bytes,
        file_name: str,
        tenant_id: str,
        doc_id: str
    ) -> UnifiedDocumentAST:
        """纯 Python 遍历 word/document.xml 的零依赖降级引擎"""
        nodes: List[ASTNode] = []
        heading_stack: List[Tuple[int, str]] = []

        try:
            with zipfile.ZipFile(io.BytesIO(content), "r") as z:
                xml_data = z.read("word/document.xml")
        except Exception as e:
            raise MalformedDocumentError(f"读取 DOCX 归档中的 word/document.xml 失败: {e}", file_name=file_name) from e

        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        try:
            root = ET.fromstring(xml_data)
        except Exception as e:
            raise MalformedDocumentError(f"DOCX XML 解析失败: {e}", file_name=file_name) from e

        body = root.find(".//w:body", ns)
        if body is None:
            body = root.find(".//body")
        elements = list(body) if body is not None else root.findall(".//w:p", ns)

        for elem in elements:
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag == "p":
                # Paragraph
                num_pr = elem.find(".//w:numPr", ns)
                num_prefix = ""
                if num_pr is not None:
                    ilvl_elem = num_pr.find("w:ilvl", ns)
                    ilvl = int(ilvl_elem.get(f"{{{ns['w']}}}val", "0")) if ilvl_elem is not None else 0
                    num_prefix = "• " if ilvl == 0 else "  - "

                texts = [t.text or "" for t in elem.findall(".//w:t", ns)]
                full_text = self.clean_text("".join(texts))
                if not full_text:
                    continue

                if num_prefix and not full_text.startswith(("•", "-", "1.", "2.", "3.", "4.")):
                    full_text = f"{num_prefix}{full_text}"

                p_style = elem.find(".//w:pStyle", ns)
                style_val = p_style.get(f"{{{ns['w']}}}val", "") if p_style is not None else ""
                
                inferred_lvl = None
                if "Heading" in style_val or "标题" in style_val:
                    m = re.search(r"\d+", style_val)
                    if m:
                        inferred_lvl = int(m.group(0))
                if not inferred_lvl:
                    inferred_lvl = self.infer_heading_level(full_text)

                if inferred_lvl:
                    section_path = self.update_section_stack(heading_stack, inferred_lvl, full_text)
                    nodes.append(
                        ASTNode(
                            block_id=self.generate_block_id("docx_h_fb"),
                            block_type=ASTBlockType.HEADING,
                            level=inferred_lvl,
                            section_path=section_path,
                            text_content=full_text,
                            page_or_sheet="1",
                            extra_metadata={"style_name": style_val}
                        )
                    )
                else:
                    is_callout = bool(re.search(r"(废标条款|重要提示|特别说明|不可偏离|严正声明|强制性标准)", full_text))
                    b_type = ASTBlockType.CALLOUT if is_callout else ASTBlockType.PARAGRAPH
                    current_path = [item[1] for item in heading_stack]
                    nodes.append(
                        ASTNode(
                            block_id=self.generate_block_id("docx_callout" if is_callout else "docx_p_fb"),
                            block_type=b_type,
                            section_path=current_path,
                            text_content=full_text,
                            page_or_sheet="1",
                            extra_metadata={"style_name": style_val}
                        )
                    )
            elif tag == "tbl":
                # Table
                tr_elems = elem.findall(".//w:tr", ns) or elem.findall(".//tr")
                grid_rows: List[List[str]] = []
                table_cells: List[TableCell] = []

                for r_idx, tr in enumerate(tr_elems):
                    tc_elems = tr.findall(".//w:tc", ns) or tr.findall(".//tc")
                    row_vals: List[str] = []
                    for c_idx, tc in enumerate(tc_elems):
                        c_texts = [t.text or "" for t in tc.findall(".//w:t", ns)]
                        ct = self.clean_text("".join(c_texts))
                        row_vals.append(ct)

                        gs_elem = tc.find(".//w:gridSpan", ns)
                        col_span = int(gs_elem.get(f"{{{ns['w']}}}val", "1")) if gs_elem is not None else 1
                        vm_elem = tc.find(".//w:vMerge", ns)
                        row_span = 1
                        if vm_elem is not None:
                            val = vm_elem.get(f"{{{ns['w']}}}val", "continue")
                            if val == "restart":
                                row_span = 2

                        table_cells.append(
                            TableCell(
                                row=r_idx,
                                col=c_idx,
                                row_span=row_span,
                                col_span=col_span,
                                text=ct,
                                is_header=(r_idx == 0)
                            )
                        )
                    grid_rows.append(row_vals)

                if grid_rows:
                    headers = [grid_rows[0]]
                    body = grid_rows[1:] if len(grid_rows) > 1 else []
                    md = self.build_table_markdown(headers=headers, rows=body)
                    current_path = [item[1] for item in heading_stack]
                    nodes.append(
                        ASTNode(
                            block_id=self.generate_block_id("docx_tbl_fb"),
                            block_type=ASTBlockType.TABLE,
                            section_path=current_path,
                            text_content=md,
                            table_data=TableData(
                                headers=headers,
                                rows=body,
                                cells=table_cells,
                                markdown=md,
                                summary=f"Word 提取表格，共 {len(body)} 行数据。"
                            ),
                            page_or_sheet="1"
                        )
                    )

        return UnifiedDocumentAST(
            document_id=doc_id,
            tenant_id=tenant_id,
            file_name=file_name,
            source_type=self.source_type,
            total_pages_or_sheets=1,
            nodes=nodes,
            metadata={"parser": "DOCXParser", "is_pure_xml_fallback": True, "extracted_nodes_count": len(nodes)}
        )

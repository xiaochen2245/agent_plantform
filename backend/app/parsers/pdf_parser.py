"""
双层 PDF、电子文件与扫描件 (PDF) 解析器
基于 PyMuPDF (fitz) 提取字符级 BoundingBox [x0, y0, x1, y1]、物理页码、
PDF 目录大纲书签 (TOC)、分栏流式排版重组与结构化表格提取。
"""

import io
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

try:
    import fitz  # PyMuPDF
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False

from app.schemas.ast import (
    ASTBlockType,
    ASTNode,
    BoundingBox,
    DocumentSourceType,
    TableCell,
    TableData,
    UnifiedDocumentAST,
)
from .base import (
    BaseParser,
    EmptyDocumentError,
    MalformedDocumentError,
    PasswordProtectedError,
)


class PDFParser(BaseParser):
    """
    PDF 视觉版面与双层文本解析适配器。
    结合 PDF 书签目录树与字块包围盒，精确捕获原文页码锚点与版面位置。
    """

    @property
    def supported_extensions(self) -> List[str]:
        return [".pdf"]

    @property
    def source_type(self) -> DocumentSourceType:
        return DocumentSourceType.PDF

    async def parse(
        self,
        content: bytes,
        file_name: str,
        tenant_id: str = "default",
        document_id: Optional[str] = None,
        **kwargs: Any,
    ) -> UnifiedDocumentAST:
        """异步解析 PDF 字节流"""
        doc_id = document_id or f"doc_{uuid.uuid4().hex[:12]}"

        if not content:
            raise EmptyDocumentError(f"PDF 文件 '{file_name}' 字节流为空", file_name=file_name)

        if not HAS_PYMUPDF:
            return self._parse_pure_regex_pdf_fallback(content, file_name, tenant_id, doc_id, **kwargs)

        try:
            doc = fitz.open(stream=content, filetype="pdf")
        except Exception as e:
            raise MalformedDocumentError(f"无法打开 PDF 文件 '{file_name}': {e}", file_name=file_name) from e

        if doc.is_encrypted:
            password = kwargs.get("password", "")
            if not doc.authenticate(password):
                doc.close()
                raise PasswordProtectedError(f"PDF 文件 '{file_name}' 受密码保护，当前凭证验证失败", file_name=file_name)

        total_pages = doc.page_count
        if total_pages == 0:
            doc.close()
            raise EmptyDocumentError(f"PDF 文件 '{file_name}' 包含 0 个页面", file_name=file_name)

        nodes: List[ASTNode] = []
        heading_stack: List[Tuple[int, str]] = []

        # 1. 提取 PDF 官方书签目录树 (Table of Contents) 作为权威大纲基准
        toc = doc.get_toc()  # [[lvl, title, page, ...], ...]
        toc_by_page: Dict[int, List[Tuple[int, str]]] = {}
        for item in toc:
            if len(item) >= 3:
                lvl, title, page_idx = item[0], item[1].strip(), item[2]
                toc_by_page.setdefault(page_idx, []).append((lvl, title))

        # 2. 逐页遍历版面元素
        for p_idx in range(total_pages):
            page_num = p_idx + 1
            page = doc[p_idx]

            # 注入当前页的书签标题
            if page_num in toc_by_page:
                for t_lvl, t_title in toc_by_page[page_num]:
                    sec_path = self.update_section_stack(heading_stack, t_lvl, t_title)
                    nodes.append(
                        ASTNode(
                            block_id=self.generate_block_id("pdf_toc_h"),
                            block_type=ASTBlockType.HEADING,
                            level=t_lvl,
                            section_path=sec_path,
                            text_content=t_title,
                            page_or_sheet=str(page_num),
                            extra_metadata={"is_toc_bookmark": True}
                        )
                    )

            # 尝试提取页面中的结构化表格 (PyMuPDF 1.23+ find_tables)
            table_bboxes: List[fitz.Rect] = []
            if hasattr(page, "find_tables"):
                try:
                    tabs = page.find_tables()
                    for tab in tabs:
                        t_node = self._build_pdf_table_node(tab, page_num, heading_stack)
                        if t_node:
                            nodes.append(t_node)
                            table_bboxes.append(fitz.Rect(tab.bbox))
                except Exception:
                    pass  # 表格探测异常平滑降级

            # 提取文本块 (blocks)
            blocks = page.get_text("blocks")
            text_blocks = [b for b in blocks if b[6] == 0]

            # 过滤掉落在已识别表格内部的纯文本块
            valid_text_blocks = []
            for b in text_blocks:
                b_rect = fitz.Rect(b[:4])
                is_inside_table = any(b_rect.intersects(t_rect) for t_rect in table_bboxes)
                if not is_inside_table:
                    valid_text_blocks.append(b)

            sorted_blocks = self._sort_reading_order(valid_text_blocks, page.rect.width)

            for b in sorted_blocks:
                b_text = self.clean_text(b[4])
                if not b_text:
                    continue

                bbox = BoundingBox(
                    x0=round(b[0], 2),
                    y0=round(b[1], 2),
                    x1=round(b[2], 2),
                    y1=round(b[3], 2),
                    page_number=page_num
                )

                inferred_lvl = self.infer_heading_level(b_text)
                if inferred_lvl:
                    sec_path = self.update_section_stack(heading_stack, inferred_lvl, b_text)
                    nodes.append(
                        ASTNode(
                            block_id=self.generate_block_id("pdf_h"),
                            block_type=ASTBlockType.HEADING,
                            level=inferred_lvl,
                            section_path=sec_path,
                            text_content=b_text,
                            page_or_sheet=str(page_num),
                            bbox=bbox
                        )
                    )
                else:
                    is_callout = bool(re.search(r"(废标条款|重要提示|特别说明|不可偏离|严正声明|强制性标准)", b_text))
                    b_type = ASTBlockType.CALLOUT if is_callout else ASTBlockType.PARAGRAPH
                    curr_path = [item[1] for item in heading_stack]

                    nodes.append(
                        ASTNode(
                            block_id=self.generate_block_id("pdf_callout" if is_callout else "pdf_p"),
                            block_type=b_type,
                            section_path=curr_path,
                            text_content=b_text,
                            page_or_sheet=str(page_num),
                            bbox=bbox
                        )
                    )

            if not valid_text_blocks and any(b[6] == 1 for b in blocks):
                nodes.append(
                    ASTNode(
                        block_id=self.generate_block_id("pdf_scan_img"),
                        block_type=ASTBlockType.PARAGRAPH,
                        section_path=[item[1] for item in heading_stack],
                        text_content="[扫描版图像页面，未嵌入可检索双层文本图层]",
                        page_or_sheet=str(page_num),
                        extra_metadata={"is_scanned_page": True, "ocr_required": True, "is_ocr": True}
                    )
                )

        doc.close()

        if not nodes:
            nodes.append(
                ASTNode(
                    block_id=self.generate_block_id("pdf_empty"),
                    block_type=ASTBlockType.PARAGRAPH,
                    text_content="[PDF 文档解析未提取到有效文本]",
                    page_or_sheet="1"
                )
            )

        return UnifiedDocumentAST(
            document_id=doc_id,
            tenant_id=tenant_id,
            file_name=file_name,
            source_type=self.source_type,
            total_pages_or_sheets=total_pages,
            nodes=nodes,
            metadata={
                "parser": "PDFParser",
                "total_pages": total_pages,
                "has_toc": len(toc) > 0,
                "extracted_nodes_count": len(nodes),
            }
        )

    def _build_pdf_table_node(
        self,
        tab: Any,
        page_num: int,
        heading_stack: List[Tuple[int, str]]
    ) -> Optional[ASTNode]:
        """构建 PDF 结构化表格节点"""
        extracted_data = tab.extract()
        if not extracted_data or len(extracted_data) < 1:
            return None

        clean_rows: List[List[str]] = []
        cells: List[TableCell] = []

        for r_idx, row in enumerate(extracted_data):
            row_vals: List[str] = []
            for c_idx, cell in enumerate(row):
                c_text = self.clean_text(cell or "")
                row_vals.append(c_text)
                cells.append(
                    TableCell(
                        row=r_idx,
                        col=c_idx,
                        row_span=1,
                        col_span=1,
                        text=c_text,
                        is_header=(r_idx == 0)
                    )
                )
            clean_rows.append(row_vals)

        headers = [clean_rows[0]] if clean_rows else []
        body = clean_rows[1:] if len(clean_rows) > 1 else []
        md = self.build_table_markdown(headers=headers, rows=body)

        b = tab.bbox
        bbox = BoundingBox(
            x0=round(b[0], 2),
            y0=round(b[1], 2),
            x1=round(b[2], 2),
            y1=round(b[3], 2),
            page_number=page_num
        )

        return ASTNode(
            block_id=self.generate_block_id("pdf_tbl"),
            block_type=ASTBlockType.TABLE,
            level=2,
            section_path=[item[1] for item in heading_stack],
            text_content=md,
            table_data=TableData(
                headers=headers,
                rows=body,
                cells=cells,
                markdown=md,
                summary=f"PDF 第 {page_num} 页提取的结构化表格，共 {len(body)} 行数据。"
            ),
            page_or_sheet=str(page_num),
            bbox=bbox
        )

    def _sort_reading_order(
        self,
        blocks: List[Tuple[Any, ...]],
        page_width: float
    ) -> List[Tuple[Any, ...]]:
        """重组阅读顺序"""
        if not blocks:
            return []

        mid_x = page_width / 2.0
        left_column = []
        right_column = []
        spans_both = []

        for b in blocks:
            x0, x1 = b[0], b[2]
            if x1 <= mid_x + 20:
                left_column.append(b)
            elif x0 >= mid_x - 20:
                right_column.append(b)
            else:
                spans_both.append(b)

        if len(left_column) >= 3 and len(right_column) >= 3 and len(spans_both) <= 2:
            left_column.sort(key=lambda b: (b[1], b[0]))
            right_column.sort(key=lambda b: (b[1], b[0]))
            spans_both.sort(key=lambda b: (b[1], b[0]))
            return left_column + right_column + spans_both

        return sorted(blocks, key=lambda b: (round(b[1] / 5.0) * 5.0, b[0]))

    def _parse_pure_regex_pdf_fallback(
        self,
        content: bytes,
        file_name: str,
        tenant_id: str,
        doc_id: str,
        **kwargs: Any,
    ) -> UnifiedDocumentAST:
        """当 PyMuPDF 缺失时的纯原生流解析降级引擎"""
        # 1. 验证魔数
        if b"%PDF-" not in content[:1024]:
            raise MalformedDocumentError(f"PDF 文件 '{file_name}' 损坏或缺少标准 %PDF- 文件头", file_name=file_name)

        # 2. 检查加密
        if b"/Encrypt" in content:
            pwd = kwargs.get("password")
            if not pwd:
                raise PasswordProtectedError(f"PDF 文件 '{file_name}' 受密码保护或已加密", file_name=file_name)

        text_latin = content.decode("utf-8", errors="replace")
        nodes: List[ASTNode] = []
        heading_stack: List[Tuple[int, str]] = []

        # 3. 提取 PDF 书签目录大纲 (/Title (...))
        toc_titles = re.findall(r"/Title\s*\(([^)]+)\)", text_latin)
        for t in toc_titles:
            clean_t = self.clean_text(t)
            if clean_t:
                lvl = self.infer_heading_level(clean_t) or 1
                sec_path = self.update_section_stack(heading_stack, lvl, clean_t)
                nodes.append(
                    ASTNode(
                        block_id=self.generate_block_id("pdf_toc_fb"),
                        block_type=ASTBlockType.HEADING,
                        level=lvl,
                        section_path=sec_path,
                        text_content=clean_t,
                        page_or_sheet="1",
                        extra_metadata={"is_toc_bookmark": True}
                    )
                )

        # 4. 提取表格（若文本流中含有结构化表格 | ... | 或明确标记）
        table_matches = re.findall(r"(\|.+?\|\r?\n\|[\s\-:|]+\|\r?\n(?:\|[^\r\n]+\|\r?\n?)+)", text_latin)
        for tbl_str in table_matches:
            lines = [l.strip() for l in tbl_str.strip().splitlines() if l.strip()]
            if len(lines) >= 3:
                header_line = lines[0]
                body_lines = lines[2:]
                headers = [[c.strip() for c in header_line.split("|")[1:-1]]]
                rows = [[c.strip() for c in bl.split("|")[1:-1]] for bl in body_lines]
                t_cells = [
                    TableCell(row=r_i, col=c_i, row_span=1, col_span=1, text=c_v, is_header=(r_i == 0))
                    for r_i, r_v in enumerate(rows) for c_i, c_v in enumerate(r_v)
                ]
                nodes.append(
                    ASTNode(
                        block_id=self.generate_block_id("pdf_tbl_fb"),
                        block_type=ASTBlockType.TABLE,
                        level=2,
                        section_path=[item[1] for item in heading_stack],
                        text_content=tbl_str,
                        table_data=TableData(headers=headers, rows=rows, cells=t_cells, markdown=tbl_str),
                        page_or_sheet="1",
                        bbox=BoundingBox(x0=50.0, y0=100.0, x1=500.0, y1=300.0, page_number=1)
                    )
                )

        # 5. 提取文本流与包围盒坐标 (BT ... ET)
        bt_blocks = re.findall(r"BT\s*(.*?)\s*ET", text_latin, re.DOTALL)
        for bt in bt_blocks:
            # 提取坐标 Tm: [a b c d e f]
            tm_match = re.search(r"([\d\.\-]+)\s+([\d\.\-]+)\s+([\d\.\-]+)\s+([\d\.\-]+)\s+([\d\.\-]+)\s+([\d\.\-]+)\s+Tm", bt)
            x0 = float(tm_match.group(5)) if tm_match else 50.0
            y0 = float(tm_match.group(6)) if tm_match else 100.0

            # 提取文本 Tj 或 TJ
            tj_texts = re.findall(r"\(([^)]+)\)\s*Tj", bt)
            if not tj_texts:
                tj_texts = re.findall(r"\(([^)]+)\)", bt)

            combined_t = self.clean_text(" ".join(tj_texts))
            if not combined_t:
                continue

            bbox = BoundingBox(x0=round(x0, 2), y0=round(y0, 2), x1=round(x0 + len(combined_t) * 10, 2), y1=round(y0 + 15.0, 2), page_number=1)

            lvl = self.infer_heading_level(combined_t)
            if lvl:
                sec_path = self.update_section_stack(heading_stack, lvl, combined_t)
                nodes.append(
                    ASTNode(
                        block_id=self.generate_block_id("pdf_h_fb"),
                        block_type=ASTBlockType.HEADING,
                        level=lvl,
                        section_path=sec_path,
                        text_content=combined_t,
                        page_or_sheet="1",
                        bbox=bbox
                    )
                )
            else:
                is_callout = bool(re.search(r"(废标条款|重要提示|特别说明|不可偏离|严正声明|强制性标准)", combined_t))
                b_type = ASTBlockType.CALLOUT if is_callout else ASTBlockType.PARAGRAPH
                nodes.append(
                    ASTNode(
                        block_id=self.generate_block_id("pdf_callout" if is_callout else "pdf_p_fb"),
                        block_type=b_type,
                        section_path=[item[1] for item in heading_stack],
                        text_content=combined_t,
                        page_or_sheet="1",
                        bbox=bbox
                    )
                )

        # 6. 检测扫描版纯图片
        if not nodes and (b"/Image" in content or b"/Subtype /Image" in content):
            nodes.append(
                ASTNode(
                    block_id=self.generate_block_id("pdf_scan_fb"),
                    block_type=ASTBlockType.PARAGRAPH,
                    section_path=["扫描版页面"],
                    text_content="[扫描版图像页面，未嵌入可检索双层文本图层]",
                    page_or_sheet="1",
                    extra_metadata={"is_scanned_page": True, "ocr_required": True, "is_ocr": True}
                )
            )

        if not nodes:
            nodes.append(
                ASTNode(
                    block_id=self.generate_block_id("pdf_empty_fb"),
                    block_type=ASTBlockType.PARAGRAPH,
                    section_path=[],
                    text_content="[PDF 文档解析未提取到有效文本]",
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
            metadata={"parser": "PDFParser", "is_regex_fallback": True, "extracted_nodes_count": len(nodes)}
        )

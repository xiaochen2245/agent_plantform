"""
AutoCAD 工程图纸 (CAD / DXF / DWG) 解析器
基于 ezdxf 解析图层、图框属性块 (INSERT/ATTRIB)、设计总说明 (TEXT/MTEXT)
与材料明细表 (TABLE/BOM)，提取抗震设防烈度、耐火等级、建筑面积与核心参数。
"""

import io
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

try:
    import ezdxf
    from ezdxf.document import Drawing
    HAS_EZDXF = True
except ImportError:
    HAS_EZDXF = False

from app.schemas.ast import (
    ASTBlockType,
    ASTNode,
    BoundingBox,
    DocumentSourceType,
    TableCell,
    TableData,
    UnifiedDocumentAST,
)
from .base import BaseParser, EmptyDocumentError, MalformedDocumentError


class CADParser(BaseParser):
    """
    CAD 图纸文本与材料表解析适配器。
    精准提取工程图纸中的图签、设计说明与材料设备清单，提供 MTEXT 控制码清洗与空间聚类。
    """

    KEY_ATTRIBUTES = {
        "工程名称", "项目名称", "图纸名称", "图名", "图号", "设计阶段",
        "抗震设防烈度", "设防烈度", "耐火等级", "建筑高度", "建筑层数",
        "结构类型", "总建筑面积", "建设单位", "设计单位", "工期"
    }

    @property
    def supported_extensions(self) -> List[str]:
        return [".dxf", ".dwg"]

    @property
    def source_type(self) -> DocumentSourceType:
        return DocumentSourceType.CAD

    async def parse(
        self,
        content: bytes,
        file_name: str,
        tenant_id: str = "default",
        document_id: Optional[str] = None,
        **kwargs: Any,
    ) -> UnifiedDocumentAST:
        """异步解析 CAD 图纸"""
        doc_id = document_id or f"doc_{uuid.uuid4().hex[:12]}"

        if not content:
            raise EmptyDocumentError(f"CAD 文件 '{file_name}' 字节流为空", file_name=file_name)

        # 检查是否为 DWG 二进制格式
        is_dwg = file_name.lower().endswith(".dwg") or content.startswith(b"AC10")
        if is_dwg and not content.decode("latin-1", errors="ignore").startswith("0\nSECTION"):
            return self._parse_dwg_fallback(content, file_name, tenant_id, doc_id)

        # DXF 流程
        return self._parse_dxf_stream(content, file_name, tenant_id, doc_id)

    def _parse_dxf_stream(
        self,
        content: bytes,
        file_name: str,
        tenant_id: str,
        doc_id: str
    ) -> UnifiedDocumentAST:
        """使用 ezdxf 或纯 Python 回退解析 DXF"""
        # 前置检查是否为合法 DXF
        raw_text = content.decode("utf-8", errors="replace")
        if "SECTION" not in raw_text and not raw_text.strip().startswith("0\n"):
            raise MalformedDocumentError(f"CAD DXF 文件 '{file_name}' 损坏或非标准 DXF", file_name=file_name)

        nodes: List[ASTNode] = []
        doc_meta: Dict[str, Any] = {
            "parser": "CADParser",
            "is_ezdxf_loaded": HAS_EZDXF,
        }

        if HAS_EZDXF:
            try:
                doc: Drawing = ezdxf.read(io.StringIO(raw_text))
                msp = doc.modelspace()

                # 1. 抽取图签/图框属性块 (INSERT with ATTRIB)
                attr_blocks = self._extract_title_block_attributes(msp)
                for blk in attr_blocks:
                    nodes.append(blk)
                    if blk.extra_metadata.get("attributes"):
                        doc_meta.update(blk.extra_metadata["attributes"])

                # 2. 抽取材料表 (TABLE 实体)
                table_blocks = self._extract_cad_tables(msp)
                nodes.extend(table_blocks)

                # 3. 抽取与空间重组 MTEXT 与 TEXT (设计总说明/注释)
                text_blocks = self._extract_cad_texts(msp)
                nodes.extend(text_blocks)

            except Exception:
                # ezdxf 解析失败，进入纯 Python 启发式抽取
                fb_nodes, fb_meta = self._fallback_dxf_scanner(raw_text)
                nodes.extend(fb_nodes)
                doc_meta.update(fb_meta)
        else:
            fb_nodes, fb_meta = self._fallback_dxf_scanner(raw_text)
            nodes.extend(fb_nodes)
            doc_meta.update(fb_meta)

        doc_meta["extracted_nodes_count"] = len(nodes)

        return UnifiedDocumentAST(
            document_id=doc_id,
            tenant_id=tenant_id,
            file_name=file_name,
            source_type=self.source_type,
            total_pages_or_sheets=1,
            nodes=nodes,
            metadata=doc_meta
        )

    def _fallback_dxf_scanner(self, dxf_str: str) -> Tuple[List[ASTNode], Dict[str, Any]]:
        """纯 Python 状态机扫描 DXF 中的 TEXT, MTEXT, INSERT(ATTRIB), TABLE 实体"""
        lines = [line.strip() for line in dxf_str.splitlines()]
        if len(lines) < 2:
            return [], {}

        nodes: List[ASTNode] = []
        doc_metadata: Dict[str, Any] = {}

        # 解析 (code, val) 对
        pairs: List[Tuple[int, str]] = []
        idx = 0
        while idx < len(lines) - 1:
            code_str = lines[idx]
            val_str = lines[idx + 1]
            try:
                code = int(code_str)
                pairs.append((code, val_str))
                idx += 2
            except ValueError:
                idx += 1

        # 状态机遍历实体
        current_entity: Optional[str] = None
        entity_props: Dict[int, List[str]] = {}
        title_block_attrs: Dict[str, str] = {}
        table_cells: List[str] = []

        def flush_entity():
            nonlocal current_entity, entity_props, title_block_attrs, table_cells
            if not current_entity:
                return

            if current_entity in ("TEXT", "MTEXT"):
                texts = entity_props.get(1, []) + entity_props.get(3, [])
                layer = entity_props.get(8, ["0"])[0]
                x_val = float(entity_props.get(10, ["0.0"])[0]) if entity_props.get(10) else 0.0
                y_val = float(entity_props.get(20, ["0.0"])[0]) if entity_props.get(20) else 0.0
                full_t = self._clean_mtext(" ".join(texts))
                if full_t and len(full_t) > 1 and not re.match(r"^[\d\.\-\s]+$", full_t):
                    bbox = BoundingBox(
                        x0=round(x_val, 2),
                        y0=round(y_val, 2),
                        x1=round(x_val + 50.0, 2),
                        y1=round(y_val + 10.0, 2),
                        page_number=1
                    )
                    nodes.append(
                        ASTNode(
                            block_id=self.generate_block_id("cad_note"),
                            block_type=ASTBlockType.CAD_NOTE,
                            section_path=["CAD 图纸设计说明", f"图层: {layer}"],
                            text_content=full_t,
                            page_or_sheet="ModelSpace",
                            bbox=bbox,
                            extra_metadata={"layer": layer, "x": x_val, "y": y_val}
                        )
                    )
            elif current_entity == "ATTRIB":
                tag = entity_props.get(2, [""])[0]
                val = self._clean_mtext(entity_props.get(1, [""])[0])
                if tag and val:
                    title_block_attrs[tag] = val
            elif current_entity == "TABLE":
                cells = [self._clean_mtext(c) for c in entity_props.get(1, []) if c]
                if cells:
                    table_cells.extend(cells)

            entity_props = {}
            current_entity = None

        for code, val in pairs:
            if code == 0:
                flush_entity()
                current_entity = val.upper()
            else:
                if current_entity:
                    entity_props.setdefault(code, []).append(val)

        flush_entity()

        # 检查图签属性
        if title_block_attrs:
            doc_metadata.update(title_block_attrs)
            matched_lines = [f"{k}: {v}" for k, v in title_block_attrs.items() if any(target in k for target in self.KEY_ATTRIBUTES)]
            if not matched_lines:
                matched_lines = [f"{k}: {v}" for k, v in title_block_attrs.items()]
            nodes.insert(
                0,
                ASTNode(
                    block_id=self.generate_block_id("cad_title_blk"),
                    block_type=ASTBlockType.CALLOUT,
                    level=2,
                    section_path=["CAD 图纸图签与工程核心属性"],
                    text_content="\n".join(matched_lines),
                    page_or_sheet="ModelSpace",
                    extra_metadata={"attributes": title_block_attrs, "is_title_block": True}
                )
            )

        # 检查提取到的材料表
        if table_cells:
            # 假设 4 列宽表格
            cols = 4 if len(table_cells) >= 4 else len(table_cells)
            rows_grid: List[List[str]] = []
            for i in range(0, len(table_cells), cols):
                rows_grid.append(table_cells[i:i + cols])
            if rows_grid:
                headers = [rows_grid[0]]
                body = rows_grid[1:] if len(rows_grid) > 1 else []
                md = self.build_table_markdown(headers=headers, rows=body, caption="CAD 材料设备表")
                t_cells = [
                    TableCell(row=r_i, col=c_i, row_span=1, col_span=1, text=c_val, is_header=(r_i == 0))
                    for r_i, r in enumerate(rows_grid) for c_i, c_val in enumerate(r)
                ]
                nodes.append(
                    ASTNode(
                        block_id=self.generate_block_id("cad_tbl"),
                        block_type=ASTBlockType.TABLE,
                        level=2,
                        section_path=["CAD 图纸材料表"],
                        text_content=md,
                        table_data=TableData(
                            headers=headers,
                            rows=body,
                            cells=t_cells,
                            markdown=md,
                            summary=f"CAD 图纸提取的结构化表格，共 {len(body)} 行。"
                        ),
                        page_or_sheet="ModelSpace"
                    )
                )

        return nodes, doc_metadata

    def _clean_mtext(self, text: str) -> str:
        """清洗 AutoCAD MTEXT 专有格式转义符"""
        if not text:
            return ""
        # 1. 替换换行代码: \P 或 \p -> \n
        t = re.sub(r"\\[Pp]", "\n", text)
        # 2. 替换工程特殊符号: %%c -> Φ, %%d -> °, %%p -> ±
        t = t.replace("%%c", "Φ").replace("%%C", "Φ")
        t = t.replace("%%d", "°").replace("%%D", "°")
        t = t.replace("%%p", "±").replace("%%P", "±")
        # 3. 清除字体/格式/颜色/对齐标签: \A1;, \fArial|...;, \C1;, \H...;
        t = re.sub(r"\\[A-Za-z0-9]+;|\\[A-Za-z0-9]+\|[^\;]+;", "", t)
        # 4. 清除上下标与堆叠转义: \S...^...;
        t = re.sub(r"\\S([^\^]+)\^([^\;]*);", r"\1/\2", t)
        # 5. 清除花括号分组
        t = t.replace("{", "").replace("}", "")
        return t.strip()

    def _extract_title_block_attributes(self, msp: Any) -> List[ASTNode]:
        """从图块引用 (INSERT) 中提取图框图签元数据"""
        results: List[ASTNode] = []
        for insert in msp.query("INSERT"):
            attribs_dict: Dict[str, str] = {}
            for attrib in insert.attribs:
                tag = str(attrib.dxf.tag).strip()
                val = self._clean_mtext(str(attrib.dxf.text).strip())
                if tag and val:
                    attribs_dict[tag] = val

            # 检查是否包含工程关键图框字段
            matched_keys = [k for k in attribs_dict.keys() if any(target in k for target in self.KEY_ATTRIBUTES)]
            if matched_keys:
                formatted_lines = [f"{k}: {attribs_dict[k]}" for k in matched_keys]
                results.append(
                    ASTNode(
                        block_id=self.generate_block_id("cad_title_blk"),
                        block_type=ASTBlockType.CALLOUT,
                        level=2,
                        section_path=["CAD 图纸图签与工程核心属性"],
                        text_content="\n".join(formatted_lines),
                        page_or_sheet="ModelSpace",
                        extra_metadata={
                            "block_name": insert.dxf.name,
                            "attributes": attribs_dict,
                            "is_title_block": True
                        }
                    )
                )
        return results

    def _extract_cad_tables(self, msp: Any) -> List[ASTNode]:
        """提取原生 CAD TABLE 实体"""
        results: List[ASTNode] = []
        for table in msp.query("TABLE"):
            n_rows = table.dxf.rows
            n_cols = table.dxf.columns
            if n_rows <= 1 or n_cols == 0:
                continue

            matrix: List[List[str]] = []
            for r in range(n_rows):
                row_texts: List[str] = []
                for c in range(n_cols):
                    cell = table.get_cell(r, c)
                    cell_text = self._clean_mtext(cell.text or "")
                    row_texts.append(cell_text)
                matrix.append(row_texts)

            headers = [matrix[0]] if matrix else []
            body = matrix[1:] if len(matrix) > 1 else []
            md = self.build_table_markdown(headers=headers, rows=body, caption="CAD 工程材料/门窗明细表")

            results.append(
                ASTNode(
                    block_id=self.generate_block_id("cad_tbl"),
                    block_type=ASTBlockType.TABLE,
                    level=2,
                    section_path=["CAD 图纸材料表"],
                    text_content=md,
                    table_data=TableData(
                        headers=headers,
                        rows=body,
                        markdown=md,
                        summary=f"CAD 图纸提取的结构化表格，共 {len(body)} 行。"
                    ),
                    page_or_sheet="ModelSpace"
                )
            )
        return results

    def _extract_cad_texts(self, msp: Any) -> List[ASTNode]:
        """提取 TEXT / MTEXT 并执行空间邻近聚类"""
        results: List[ASTNode] = []
        raw_items: List[Dict[str, Any]] = []

        for entity in msp.query("TEXT MTEXT"):
            text = ""
            if entity.dxftype() == "MTEXT":
                text = self._clean_mtext(entity.text)
            else:
                text = self._clean_mtext(entity.dxf.text)

            if not text or len(text) < 2:
                continue

            # 插入点坐标
            insert_pt = entity.dxf.insert
            layer = entity.dxf.layer

            raw_items.append({
                "text": text,
                "x": insert_pt[0],
                "y": insert_pt[1],
                "layer": layer,
            })

        if not raw_items:
            return results

        # 按图层分类
        by_layer: Dict[str, List[Dict[str, Any]]] = {}
        for item in raw_items:
            by_layer.setdefault(item["layer"], []).append(item)

        for layer, items in by_layer.items():
            # 图层内按照 Y 坐标自上而下 (降序)，X 坐标自左至右排序
            items.sort(key=lambda i: (-i["y"], i["x"]))
            
            # 将邻近多行拼接为一个逻辑段落
            combined_text = "\n".join([it["text"] for it in items])
            if combined_text:
                results.append(
                    ASTNode(
                        block_id=self.generate_block_id("cad_note"),
                        block_type=ASTBlockType.CAD_NOTE,
                        section_path=["CAD 图纸设计说明", f"图层: {layer}"],
                        text_content=combined_text,
                        page_or_sheet="ModelSpace",
                        extra_metadata={"layer": layer, "entity_count": len(items)}
                    )
                )

        return results

    def _fallback_dxf_text_scanner(self, dxf_str: str) -> List[ASTNode]:
        """纯 Python 正则扫描 DXF 中的组码 1 (文字内容) 与 3 (长 MTEXT)"""
        text_matches = re.findall(r"\n\s*1\n([^\r\n]+)", dxf_str)
        cleaned = [self._clean_mtext(t) for t in text_matches if len(t.strip()) > 1]
        
        if not cleaned:
            return []

        # 过滤掉纯数字或内部格式标记
        valid_texts = [t for t in cleaned if not re.match(r"^[\d\.\-\s]+$", t)]
        return [
            ASTNode(
                block_id=self.generate_block_id("cad_fallback_note"),
                block_type=ASTBlockType.CAD_NOTE,
                section_path=["CAD 图纸文本 (原生扫描提取)"],
                text_content="\n".join(valid_texts[:200]),
                page_or_sheet="ModelSpace"
            )
        ]

    def _parse_dwg_fallback(
        self,
        content: bytes,
        file_name: str,
        tenant_id: str,
        doc_id: str
    ) -> UnifiedDocumentAST:
        """针对专有二进制 DWG 的兼容降级处理"""
        # 扫描 DWG 二进制流中可能嵌入的 UTF-16LE / UTF-8 字符
        decoded_sample = content.decode("latin-1", errors="ignore")
        found_chinese = re.findall(r"[\u4e00-\u9fa5]{2,}", decoded_sample)

        notice = (
            f"文件 '{file_name}' 为 AutoCAD 私有二进制 DWG 格式。"
            f"推荐在 CAD 软件中将图纸导出为标准 DXF 格式 (*.dxf) 以获取完整图层及材料明细表解析保真度。"
        )
        nodes = [
            ASTNode(
                block_id=self.generate_block_id("cad_dwg_warn"),
                block_type=ASTBlockType.CALLOUT,
                section_path=["DWG 图纸解析说明"],
                text_content=notice,
                page_or_sheet="ModelSpace"
            )
        ]
        if found_chinese:
            nodes.append(
                ASTNode(
                    block_id=self.generate_block_id("cad_dwg_strings"),
                    block_type=ASTBlockType.CAD_NOTE,
                    section_path=["DWG 探测文本"],
                    text_content=" ".join(found_chinese[:50]),
                    page_or_sheet="ModelSpace"
                )
            )

        return UnifiedDocumentAST(
            document_id=doc_id,
            tenant_id=tenant_id,
            file_name=file_name,
            source_type=self.source_type,
            total_pages_or_sheets=1,
            nodes=nodes,
            metadata={"parser": "CADParser", "is_dwg_fallback": True}
        )

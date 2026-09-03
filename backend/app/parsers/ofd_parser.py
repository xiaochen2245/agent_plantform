"""
中国国家标准版式电子文件 (OFD - GB/T 38330-2019 / GB/T 33190-2016) 解析器
基于纯 Python 原生 zipfile 与 xml.etree.ElementTree 流式解析，提取版面文本、红头公文头、图章批注与绝对视觉坐标。
"""

import io
import re
import uuid
import xml.etree.ElementTree as ET
import zipfile
from typing import Any, Dict, List, Optional, Tuple

from app.schemas.ast import (
    ASTBlockType,
    ASTNode,
    BoundingBox,
    DocumentSourceType,
    UnifiedDocumentAST,
)
from .base import BaseParser, EmptyDocumentError, MalformedDocumentError


class OFDParser(BaseParser):
    """
    OFD 国标版式公文解析适配器。
    纯原生实现，无需依赖外部编译库。精确解析多层级 XML 树，重组自然阅读流。
    """

    OFD_NS = {
        "ofd": "http://www.ofdspec.org/2016",
    }

    @property
    def supported_extensions(self) -> List[str]:
        return [".ofd"]

    @property
    def source_type(self) -> DocumentSourceType:
        return DocumentSourceType.OFD

    async def parse(
        self,
        content: bytes,
        file_name: str,
        tenant_id: str = "default",
        document_id: Optional[str] = None,
        **kwargs: Any,
    ) -> UnifiedDocumentAST:
        """解析 OFD 字节流为 UnifiedDocumentAST"""
        doc_id = document_id or f"doc_{uuid.uuid4().hex[:12]}"
        
        if not content:
            raise EmptyDocumentError(f"OFD 文件 '{file_name}' 字节流为空", file_name=file_name)

        try:
            with zipfile.ZipFile(io.BytesIO(content), "r") as z:
                return self._parse_ofd_zip(z, file_name, tenant_id, doc_id)
        except zipfile.BadZipFile as e:
            raise MalformedDocumentError(f"OFD 文件 '{file_name}' 不是合法的 ZIP 归档: {e}", file_name=file_name) from e
        except Exception as e:
            if isinstance(e, (EmptyDocumentError, MalformedDocumentError)):
                raise
            raise MalformedDocumentError(f"解析 OFD 文档 '{file_name}' 失败: {e}", file_name=file_name) from e

    def _parse_ofd_zip(
        self,
        z: zipfile.ZipFile,
        file_name: str,
        tenant_id: str,
        doc_id: str
    ) -> UnifiedDocumentAST:
        # 1. 验证 OFD.xml 根索引
        try:
            ofd_xml_content = z.read("OFD.xml")
        except KeyError:
            raise MalformedDocumentError("OFD 归档中缺失根文件 'OFD.xml'", file_name=file_name)

        root_elem = ET.fromstring(ofd_xml_content)
        # 查找 Doc_0 根路径
        doc_body = root_elem.find(".//ofd:DocBody", self.OFD_NS)
        if doc_body is None:
            doc_body = root_elem.find(".//DocBody")
        doc_root_loc = "Doc_0/Document.xml"
        if doc_body is not None:
            doc_root = doc_body.find("ofd:DocRoot", self.OFD_NS)
            if doc_root is None:
                doc_root = doc_body.find("DocRoot")
            if doc_root is not None and doc_root.text:
                doc_root_loc = doc_root.text.strip().lstrip("/")

        # 2. 读取 Document.xml 获取页面清单
        doc_base_dir = doc_root_loc.rsplit("/", 1)[0] if "/" in doc_root_loc else "Doc_0"
        try:
            doc_xml_content = z.read(doc_root_loc)
        except KeyError:
            # 兼容非标准路径回退
            candidates = [n for n in z.namelist() if n.endswith("Document.xml")]
            if candidates:
                doc_xml_content = z.read(candidates[0])
                doc_base_dir = candidates[0].rsplit("/", 1)[0]
            else:
                raise MalformedDocumentError(f"未找到 OFD 文档结构文件: {doc_root_loc}", file_name=file_name)

        doc_elem = ET.fromstring(doc_xml_content)
        page_elems = doc_elem.findall(".//ofd:Page", self.OFD_NS) or doc_elem.findall(".//Page")

        nodes: List[ASTNode] = []
        heading_stack: List[Tuple[int, str]] = []
        total_pages = len(page_elems) if page_elems else 1
        page_num = 1

        # 3. 逐页提取图元与文字流
        for p_elem in page_elems:
            base_loc = p_elem.get("BaseLoc")
            if not base_loc:
                continue
            
            # 规范化相对路径
            page_path = f"{doc_base_dir}/{base_loc}".replace("//", "/")
            if page_path not in z.namelist():
                # 尝试直接使用 base_loc
                page_path = base_loc.lstrip("/")

            if page_path in z.namelist():
                page_xml = z.read(page_path)
                page_nodes = self._parse_page_content(
                    page_xml=page_xml,
                    page_num=page_num,
                    heading_stack=heading_stack
                )
                nodes.extend(page_nodes)
            page_num += 1

        # 4. 检查是否有印章批注与红头公文元数据
        stamp_notes = self._extract_stamps_and_signs(z)
        if stamp_notes:
            for s_text in stamp_notes:
                nodes.append(
                    ASTNode(
                        block_id=self.generate_block_id("ofd_stamp"),
                        block_type=ASTBlockType.CALLOUT,
                        text_content=s_text,
                        section_path=["公文签章与认证信息"],
                        extra_metadata={"type": "stamp_or_signature"}
                    )
                )

        if not nodes:
            # 容错：全文档可能由图片组成
            nodes.append(
                ASTNode(
                    block_id=self.generate_block_id("ofd_empty"),
                    block_type=ASTBlockType.PARAGRAPH,
                    text_content="[OFD 文档包含图形或扫描光栅图层，未提取到纯文本]",
                    page_or_sheet="1",
                    extra_metadata={"is_scanned_or_vector": True}
                )
            )

        return UnifiedDocumentAST(
            document_id=doc_id,
            tenant_id=tenant_id,
            file_name=file_name,
            source_type=self.source_type,
            total_pages_or_sheets=max(total_pages, 1),
            nodes=nodes,
            metadata={
                "parser": "OFDParser",
                "standard": "GB/T 38330-2019",
                "extracted_nodes_count": len(nodes),
            }
        )

    def _parse_page_content(
        self,
        page_xml: bytes,
        page_num: int,
        heading_stack: List[Tuple[int, str]]
    ) -> List[ASTNode]:
        """解析单页 Content.xml"""
        root = ET.fromstring(page_xml)
        raw_text_items: List[Dict[str, Any]] = []

        # 遍历所有 TextObject
        for obj in root.findall(".//ofd:TextObject", self.OFD_NS) or root.findall(".//TextObject"):
            boundary_str = obj.get("Boundary", "")
            coords = self._parse_boundary(boundary_str)
            
            # 提取文字
            text_codes = obj.findall("ofd:TextCode", self.OFD_NS) or obj.findall("TextCode")
            text = "".join([tc.text or "" for tc in text_codes]).strip()
            
            if text:
                raw_text_items.append({
                    "text": text,
                    "bbox": coords,
                    "x0": coords.x0 if coords else 0.0,
                    "y0": coords.y0 if coords else 0.0,
                    "y1": coords.y1 if coords else 0.0,
                })

        # 按照 Y 坐标自然行聚类 (容差 3.5pt) -> X 坐标从左到右排序
        raw_text_items.sort(key=lambda item: (round(item["y0"] / 4.0) * 4.0, item["x0"]))

        page_nodes: List[ASTNode] = []
        if not raw_text_items:
            return page_nodes

        # 行聚合与段落重组
        current_line_parts: List[str] = []
        current_bboxes: List[BoundingBox] = []
        last_y0 = -1.0
        line_height_threshold = 4.0

        for item in raw_text_items:
            text = item["text"]
            bbox = item["bbox"]
            y0 = item["y0"]

            if last_y0 >= 0 and abs(y0 - last_y0) > line_height_threshold:
                # 换行，组装上一行
                line_text = " ".join(current_line_parts).strip()
                if line_text:
                    node = self._create_node_from_line(
                        line_text=line_text,
                        bboxes=current_bboxes,
                        page_num=page_num,
                        heading_stack=heading_stack
                    )
                    page_nodes.append(node)
                current_line_parts = [text]
                current_bboxes = [bbox] if bbox else []
            else:
                current_line_parts.append(text)
                if bbox:
                    current_bboxes.append(bbox)
            last_y0 = y0

        # 处理末尾遗留行
        if current_line_parts:
            line_text = " ".join(current_line_parts).strip()
            if line_text:
                node = self._create_node_from_line(
                    line_text=line_text,
                    bboxes=current_bboxes,
                    page_num=page_num,
                    heading_stack=heading_stack
                )
                page_nodes.append(node)

        return page_nodes

    def _create_node_from_line(
        self,
        line_text: str,
        bboxes: List[BoundingBox],
        page_num: int,
        heading_stack: List[Tuple[int, str]]
    ) -> ASTNode:
        """基于行文本构建 ASTNode，自动判定标题与红头公文标记"""
        clean_t = self.clean_text(line_text)
        merged_bbox: Optional[BoundingBox] = None
        if bboxes:
            merged_bbox = BoundingBox(
                x0=min(b.x0 for b in bboxes),
                y0=min(b.y0 for b in bboxes),
                x1=max(b.x1 for b in bboxes),
                y1=max(b.y1 for b in bboxes),
                page_number=page_num
            )

        # 检查红头公文头 (例如: "发文字号", "国发〔2026〕", "特急", "绝密")
        if re.search(r"(〔\d{4}〕|\b发文字号\b|绝密|机密|特急|加急)", clean_t):
            return ASTNode(
                block_id=self.generate_block_id("ofd_redheader"),
                block_type=ASTBlockType.CALLOUT,
                text_content=clean_t,
                section_path=["红头公文要素"],
                page_or_sheet=str(page_num),
                bbox=merged_bbox,
                extra_metadata={"is_official_header": True}
            )

        # 检查是否为大纲标题
        inferred_lvl = self.infer_heading_level(clean_t)
        if inferred_lvl:
            section_path = self.update_section_stack(heading_stack, inferred_lvl, clean_t)
            return ASTNode(
                block_id=self.generate_block_id("ofd_head"),
                block_type=ASTBlockType.HEADING,
                level=inferred_lvl,
                section_path=section_path,
                text_content=clean_t,
                page_or_sheet=str(page_num),
                bbox=merged_bbox
            )

        # 普通正文
        current_path = [item[1] for item in heading_stack]
        return ASTNode(
            block_id=self.generate_block_id("ofd_p"),
            block_type=ASTBlockType.PARAGRAPH,
            section_path=current_path,
            text_content=clean_t,
            page_or_sheet=str(page_num),
            bbox=merged_bbox
        )

    def _parse_boundary(self, boundary_str: str) -> Optional[BoundingBox]:
        """解析 OFD Boundary='x y w h' 字符串"""
        if not boundary_str:
            return None
        parts = [p for p in boundary_str.strip().split() if p]
        if len(parts) >= 4:
            try:
                x = float(parts[0])
                y = float(parts[1])
                w = float(parts[2])
                h = float(parts[3])
                return BoundingBox(x0=x, y0=y, x1=x + w, y1=y + h, page_number=1)
            except ValueError:
                return None
        return None

    def _extract_stamps_and_signs(self, z: zipfile.ZipFile) -> List[str]:
        """扫描 OFD 中的电子印章签名数据"""
        stamp_summaries: List[str] = []
        sign_files = [n for n in z.namelist() if "Signs/Sign_" in n and n.endswith(".xml")]
        for sf in sign_files:
            try:
                xml_data = z.read(sf)
                root = ET.fromstring(xml_data)
                provider = root.findtext(".//ofd:Provider", namespaces=self.OFD_NS) or "国密安全电子印章"
                seal_name = root.findtext(".//ofd:SealName", namespaces=self.OFD_NS) or ""
                stamp_summaries.append(f"电子印章/电子签名: {seal_name} (签发服务商: {provider})")
            except Exception:
                pass
        return stamp_summaries

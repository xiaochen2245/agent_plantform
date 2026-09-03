"""
工程施工进度计划与网络图 (MPP / MSPDI XML) 解析器
基于 Microsoft Project XML (MSPDI) 标准，提取任务 WBS 拓扑、工期历日、开竣工日期、
前置依赖关系 (Predecessor Links) 与关键路径 (Critical Path)，映射为 ScheduleTaskData。
"""

import io
import re
import uuid
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

from app.schemas.ast import (
    ASTBlockType,
    ASTNode,
    DocumentSourceType,
    ScheduleTaskData,
    TableData,
    UnifiedDocumentAST,
)
from .base import BaseParser, EmptyDocumentError, MalformedDocumentError


class MPPParser(BaseParser):
    """
    MPP / Project 进度计划解析适配器。
    原生支持标准 MSPDI XML 结构，并提供对二进制 .mpp 容器的鲁棒嗅探与兼容降级。
    """

    MSPDI_NS = {
        "ms": "http://schemas.microsoft.com/project",
    }

    @property
    def supported_extensions(self) -> List[str]:
        return [".mpp", ".xml"]

    @property
    def source_type(self) -> DocumentSourceType:
        return DocumentSourceType.MPP

    async def parse(
        self,
        content: bytes,
        file_name: str,
        tenant_id: str = "default",
        document_id: Optional[str] = None,
        **kwargs: Any,
    ) -> UnifiedDocumentAST:
        """异步解析进度计划"""
        doc_id = document_id or f"doc_{uuid.uuid4().hex[:12]}"

        if not content:
            raise EmptyDocumentError(f"进度计划文件 '{file_name}' 内容为空", file_name=file_name)

        # 1. 检查是否为标准 XML (MSPDI)
        content_header = content[:512]
        if b"<?xml" in content_header or b"<Project" in content_header:
            return self._parse_mspdi_xml(content, file_name, tenant_id, doc_id)

        # 2. 检查是否为二进制 .mpp (OLE 复合文档)
        if content.startswith(b"\xd0\xcf\x11\xe0"):
            return self._parse_binary_mpp_fallback(content, file_name, tenant_id, doc_id)

        # 3. 尝试直接作为 XML 解析
        try:
            return self._parse_mspdi_xml(content, file_name, tenant_id, doc_id)
        except Exception as e:
            raise MalformedDocumentError(
                f"无法解析进度计划文件 '{file_name}': 既不是有效 MSPDI XML 也不是支持的 OLE 格式 ({e})",
                file_name=file_name
            ) from e

    def _parse_mspdi_xml(
        self,
        content: bytes,
        file_name: str,
        tenant_id: str,
        doc_id: str
    ) -> UnifiedDocumentAST:
        """解析标准 MSPDI XML 树"""
        try:
            # 兼容处理命名空间前缀或默认无命名空间 XML
            root = ET.fromstring(content)
        except ET.ParseError as e:
            raise MalformedDocumentError(f"MSPDI XML 解析语法错误: {e}", file_name=file_name) from e

        # 消除命名空间标签前缀干扰
        def strip_ns(tag: str) -> str:
            return tag.split("}")[-1] if "}" in tag else tag

        # 提取项目全局元数据
        project_title = "工程进度计划"
        start_date = ""
        finish_date = ""

        for child in root:
            t = strip_ns(child.tag)
            if t == "Title" and child.text:
                project_title = child.text.strip()
            elif t == "StartDate" and child.text:
                start_date = child.text.strip()
            elif t == "FinishDate" and child.text:
                finish_date = child.text.strip()

        tasks_elem = None
        for child in root:
            if strip_ns(child.tag) == "Tasks":
                tasks_elem = child
                break

        if tasks_elem is None:
            raise MalformedDocumentError("MSPDI XML 中未检索到 <Tasks> 节点列表", file_name=file_name)

        nodes: List[ASTNode] = []
        wbs_stack: List[Tuple[int, str]] = []
        summary_rows: List[List[str]] = []

        # 遍历任务
        for task in tasks_elem:
            if strip_ns(task.tag) != "Task":
                continue

            task_dict: Dict[str, Any] = {}
            preds: List[str] = []

            for elem in task:
                tag_name = strip_ns(elem.tag)
                if tag_name == "PredecessorLink":
                    pred_uid = elem.findtext("PredecessorUID") or elem.findtext(f"{{{self.MSPDI_NS['ms']}}}PredecessorUID")
                    if pred_uid:
                        preds.append(pred_uid.strip())
                else:
                    task_dict[tag_name] = (elem.text or "").strip()

            uid = task_dict.get("UID") or str(uuid.uuid4().hex[:8])
            name = task_dict.get("Name", "").strip()
            if not name:
                continue

            outline_level = int(task_dict.get("OutlineLevel", "1") or "1")
            outline_num = task_dict.get("OutlineNumber", "")
            duration_raw = task_dict.get("Duration", "")
            duration_days = self._parse_iso_duration_to_days(duration_raw)
            t_start = task_dict.get("Start", "")
            t_finish = task_dict.get("Finish", "")
            is_critical = (task_dict.get("Critical", "0") == "1")

            # 维护 WBS 层级栈
            display_title = f"{outline_num} {name}".strip() if outline_num else name
            section_path = self.update_section_stack(wbs_stack, outline_level, display_title)

            task_data = ScheduleTaskData(
                task_id=uid,
                task_name=name,
                duration_days=duration_days,
                start_date=t_start,
                finish_date=t_finish,
                is_critical_path=is_critical,
                predecessors=preds
            )

            # 节点描述
            desc_lines = [
                f"任务名称: {name}",
                f"WBS层级: {outline_level} ({outline_num})",
                f"工期: {duration_days} 日历天",
                f"计划开工: {t_start or '未指定'}",
                f"计划完工: {t_finish or '未指定'}",
                f"关键路径: {'是 (Critical)' if is_critical else '否'}",
            ]
            if preds:
                desc_lines.append(f"前置紧前任务 UID: {', '.join(preds)}")

            node = ASTNode(
                block_id=f"task_{uid}",
                block_type=ASTBlockType.SCHEDULE_TASK,
                level=outline_level,
                section_path=section_path,
                text_content="; ".join(desc_lines),
                schedule_data=task_data,
                page_or_sheet="GanttChart",
                extra_metadata={
                    "uid": uid,
                    "wbs_number": outline_num,
                    "is_critical": is_critical,
                }
            )
            nodes.append(node)

            # 汇总进全局甘特图表格
            summary_rows.append([
                outline_num or uid,
                name,
                f"{duration_days} 天",
                t_start[:10] if len(t_start) >= 10 else t_start,
                t_finish[:10] if len(t_finish) >= 10 else t_finish,
                "是" if is_critical else "否",
                ", ".join(preds) if preds else "-"
            ])

        # 在顶层构建一个高保真全局工期控制网络 Markdown 表格节点
        headers = [["WBS/序号", "任务名称", "工期", "计划开始", "计划完成", "关键路径", "紧前任务"]]
        summary_markdown = self.build_table_markdown(
            headers=headers,
            rows=summary_rows,
            caption=f"工程进度计划总览: {project_title}"
        )

        overview_node = ASTNode(
            block_id=self.generate_block_id("mpp_overview"),
            block_type=ASTBlockType.TABLE,
            level=1,
            section_path=[project_title, "工程进度总表"],
            text_content=summary_markdown,
            table_data=TableData(
                headers=headers,
                rows=summary_rows,
                markdown=summary_markdown,
                summary=f"{project_title} 全量进度计划，包含 {len(summary_rows)} 个分解任务节点。"
            ),
            page_or_sheet="ProjectSummary"
        )
        nodes.insert(0, overview_node)

        return UnifiedDocumentAST(
            document_id=doc_id,
            tenant_id=tenant_id,
            file_name=file_name,
            source_type=self.source_type,
            total_pages_or_sheets=1,
            nodes=nodes,
            metadata={
                "parser": "MPPParser",
                "project_title": project_title,
                "start_date": start_date,
                "finish_date": finish_date,
                "total_tasks": len(summary_rows),
            }
        )

    def _parse_binary_mpp_fallback(
        self,
        content: bytes,
        file_name: str,
        tenant_id: str,
        doc_id: str
    ) -> UnifiedDocumentAST:
        """
        针对二进制 .mpp (OLE Structured Storage) 的平滑降级策略。
        纯 Python 提取可打印 ASCII/Unicode 字符串，并给出推荐的导出标准 XML 引导提示。
        """
        # 提取其中可能嵌入的可读文本片段
        text_chunks = re.findall(r"[\u4e00-\u9fa5a-zA-Z0-9_\-\.\:]{4,}", content.decode("latin-1", errors="ignore"))
        filtered_texts = [t for t in text_chunks if len(t) > 4][:100]

        nodes = [
            ASTNode(
                block_id=self.generate_block_id("mpp_ole_warn"),
                block_type=ASTBlockType.CALLOUT,
                level=1,
                section_path=["二进制 MPP 解析说明"],
                text_content=(
                    f"文件 '{file_name}' 为微软私有二进制 OLE 复合格式。建议在 MS Project 中通过 '另存为 -> XML 格式 (*.xml)' "
                    f"导出以获得 100% 关键路径与依赖关系保真度。当前已启用纯原生提取兼容模式。"
                ),
                page_or_sheet="Fallback"
            )
        ]

        if filtered_texts:
            nodes.append(
                ASTNode(
                    block_id=self.generate_block_id("mpp_raw_strings"),
                    block_type=ASTBlockType.PARAGRAPH,
                    section_path=["二进制 MPP 提取文本"],
                    text_content="\n".join(filtered_texts),
                    page_or_sheet="Fallback"
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
                "parser": "MPPParser",
                "is_binary_ole_fallback": True,
            }
        )

    def _parse_iso_duration_to_days(self, duration_str: str) -> float:
        """
        将 ISO 8601 或 Project 持续时间 (如 PT720H0M0S, PT90D) 换算为标准日历天。
        标准工时转换: 8 小时 = 1 工作日。
        """
        if not duration_str:
            return 0.0

        # 直接天数匹配: PT90D
        match_days = re.search(r"(\d+(?:\.\d+)?)\s*D", duration_str, re.IGNORECASE)
        if match_days:
            return float(match_days.group(1))

        # 小时匹配: PT720H
        match_hours = re.search(r"(\d+(?:\.\d+)?)\s*H", duration_str, re.IGNORECASE)
        if match_hours:
            hours = float(match_hours.group(1))
            # 8小时折算1天
            return round(hours / 8.0, 2)

        # 纯数字提取 fallback
        digits = re.findall(r"\d+", duration_str)
        if digits:
            return float(digits[0])

        return 0.0

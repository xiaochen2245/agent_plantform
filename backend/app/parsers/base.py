"""
统一多源异构文档解析集群基类与接口规范 (Base Parser Interface)
定义 BaseParser 抽象基类、通用解析异常体系以及通用表格/大纲格式化工具函数。
"""

from abc import ABC, abstractmethod
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from app.schemas.ast import (
    ASTBlockType,
    ASTNode,
    BoundingBox,
    DocumentSourceType,
    TableCell,
    TableData,
    UnifiedDocumentAST,
)


# =====================================================================
# 1. 异常定义体系 (Parser Exception Hierarchy)
# =====================================================================

class ParserError(Exception):
    """解析器基础异常"""
    def __init__(self, message: str, file_name: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.file_name = file_name
        self.details = details or {}

    def __str__(self) -> str:
        ctx = f" [File: {self.file_name}]" if self.file_name else ""
        return f"{self.__class__.__name__}: {self.message}{ctx}"


class UnsupportedFormatException(ParserError):
    """不支持的文件格式或扩展名"""
    pass


class MalformedDocumentError(ParserError):
    """文档格式损坏、归档破损或 XML/二进制流非法"""
    pass


class EmptyDocumentError(ParserError):
    """文档内容为空 (0 字节、0 页面或无有效字符)"""
    pass


class PasswordProtectedError(ParserError):
    """文档受密码保护或加密无法解密"""
    pass


# =====================================================================
# 2. 抽象基类 (BaseParser)
# =====================================================================

class BaseParser(ABC):
    """
    所有特定格式解析器的抽象基类。
    统一接收二进制数据流或文件路径，执行拓扑抽取，输出 UnifiedDocumentAST。
    """

    @property
    @abstractmethod
    def supported_extensions(self) -> List[str]:
        """该解析器支持的文件扩展名列表 (全部小写，带点，如 ['.docx', '.doc'])"""
        pass

    @property
    @abstractmethod
    def source_type(self) -> DocumentSourceType:
        """对应的 DocumentSourceType 枚举"""
        pass

    @abstractmethod
    async def parse(
        self,
        content: bytes,
        file_name: str,
        tenant_id: str = "default",
        document_id: Optional[str] = None,
        **kwargs: Any,
    ) -> UnifiedDocumentAST:
        """
        核心解析入口方法 (异步协程，支持高并发解析与非阻塞 CPU 密集任务调度)。

        :param content: 文件的原始二进制字节流
        :param file_name: 原始文件名（用于推断类型与元数据溯源）
        :param tenant_id: 多租户隔离租户 ID
        :param document_id: 可选的文档全局唯一 ID，未传入时自动生成
        :param kwargs: 额外参数（如密码 password、ocr_enabled、max_pages 等）
        :return: 严格遵循 Pydantic v2 规范的 UnifiedDocumentAST
        :raises ParserError 及派生子类
        """
        pass

    # -----------------------------------------------------------------
    # 通用辅助工具函数 (Utilities)
    # -----------------------------------------------------------------

    def generate_block_id(self, prefix: str = "blk") -> str:
        """生成全局唯一的块 ID"""
        return f"{prefix}_{uuid.uuid4().hex[:12]}"

    def clean_text(self, text: Optional[str]) -> str:
        """清理文本空白字符与非打印控制字符，保留换行语义"""
        if not text:
            return ""
        # 统一将各种特殊空白字符转为标准空格，但保留换行符
        cleaned = re.sub(r"[\r\t\f\v ]+", " ", text)
        cleaned = re.sub(r" \n", "\n", cleaned)
        cleaned = re.sub(r"\n ", "\n", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    def infer_heading_level(self, text: str) -> Optional[int]:
        """
        基于中文与数字编号规则，启发式推断段落的大纲标题级别 (1~9)。
        未匹配则返回 None。
        """
        text = text.strip()
        if not text or len(text) > 120:  # 超过 120 字通常为正文段落
            return None

        # 1 级标题: "第一章", "第1章", "一、", "PART 1"
        if re.match(r"^(第[一二三四五六七八九十百]+[章节篇部卷]|第\s*\d+\s*[章节篇部卷]|CHAPTER\s*\d+|[一二三四五六七八九十]+[、.．])\s*", text, re.IGNORECASE):
            return 1

        # 2 级标题: "1.1", "第二节", "（一）", "(一)"
        if re.match(r"^(\d+\.\d+(?!\.)|第[一二三四五六七八九十百]+节|[（(][一二三四五六七八九十]+[）)])\s*", text):
            return 2

        # 3 级标题: "1.1.1", "1、", "1."
        if re.match(r"^(\d+\.\d+\.\d+(?!\.)|\d+[、.．])\s*", text):
            return 3

        # 4 级标题: "1.1.1.1", "（1）", "(1)"
        if re.match(r"^(\d+\.\d+\.\d+\.\d+(?!\.)|[（(]\d+[）)])\s*", text):
            return 4

        # 5 级标题: "1.1.1.1.1", "①", "a."
        if re.match(r"^(\d+\.\d+\.\d+\.\d+\.\d+|[①②③④⑤⑥⑦⑧⑨⑩]|[a-zA-Z][、.．])\s*", text):
            return 5

        return None

    def update_section_stack(
        self,
        current_stack: List[Tuple[int, str]],
        new_level: int,
        heading_title: str
    ) -> List[str]:
        """
        维护当前处于激活状态的层级大纲栈，生成当前块的完整 section_path 面包屑。
        例如：['第一章 施工部署', '1.2 进度规划']
        """
        clean_title = self.clean_text(heading_title)
        # 弹出所有大于或等于当前级别的兄弟或子节点
        while current_stack and current_stack[-1][0] >= new_level:
            current_stack.pop()
        current_stack.append((new_level, clean_title))
        return [item[1] for item in current_stack]

    def build_table_markdown(
        self,
        headers: List[List[str]],
        rows: List[List[str]],
        caption: Optional[str] = None
    ) -> str:
        """
        将二维表头与二维单元格文本生成高质量、保真的 Markdown 表格字符串。
        对空行进行过滤，对包含换行符的单元格转义为 <br/>，确保 LLM 视界对齐。
        """
        lines: List[str] = []
        if caption:
            lines.append(f"**{caption.strip()}**\n")

        # 确定最大列数
        max_cols = 0
        if headers:
            max_cols = max(max_cols, max(len(h) for h in headers))
        if rows:
            max_cols = max(max_cols, max(len(r) for r in rows))

        if max_cols == 0:
            return ""

        def normalize_cell(val: Any) -> str:
            if val is None:
                return ""
            s = str(val).strip()
            # 转义 Markdown 管道符与换行符
            s = s.replace("|", "&#124;").replace("\n", "<br/>")
            return s

        # 1. 写入表头
        if headers:
            for h_idx, header_row in enumerate(headers):
                padded = [normalize_cell(c) for c in header_row] + [""] * (max_cols - len(header_row))
                lines.append("| " + " | ".join(padded) + " |")
                if h_idx == len(headers) - 1:
                    # 写入分隔线
                    lines.append("| " + " | ".join(["---"] * max_cols) + " |")
        else:
            # 无显式表头，使用首行作为表头或生成 Col 1..Col N
            if rows:
                first_row = [normalize_cell(c) for c in rows[0]] + [""] * (max_cols - len(rows[0]))
                lines.append("| " + " | ".join(first_row) + " |")
                lines.append("| " + " | ".join(["---"] * max_cols) + " |")
                rows = rows[1:]
            else:
                default_header = [f"列{i+1}" for i in range(max_cols)]
                lines.append("| " + " | ".join(default_header) + " |")
                lines.append("| " + " | ".join(["---"] * max_cols) + " |")

        # 2. 写入数据行
        for row in rows:
            # 忽略全空行
            if not any(row):
                continue
            padded = [normalize_cell(c) for c in row] + [""] * (max_cols - len(row))
            lines.append("| " + " | ".join(padded) + " |")

        return "\n".join(lines)

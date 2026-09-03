"""
解析器集群工厂与动态路由调度中心 (Parser Cluster Factory)
提供基于文件扩展名派发、文件魔数嗅探 (Magic Sniffing) 以及降级容错机制。
"""

import io
import os
import zipfile
from typing import Any, Dict, List, Optional, Type

from app.schemas.ast import UnifiedDocumentAST
from .base import (
    BaseParser,
    EmptyDocumentError,
    MalformedDocumentError,
    ParserError,
    UnsupportedFormatException,
)
from .cad_parser import CADParser
from .docx_parser import DOCXParser
from .mpp_parser import MPPParser
from .ofd_parser import OFDParser
from .pdf_parser import PDFParser
from .pptx_parser import PPTXParser
from .xlsx_parser import XLSXParser


class ParserFactory:
    """
    文档解析器工厂类。
    统一管理多格式解析适配器实例，提供按文件扩展名派发与魔数探测双保险机制。
    """

    def __init__(self) -> None:
        self._registry: Dict[str, BaseParser] = {}
        self._init_default_parsers()

    def _init_default_parsers(self) -> None:
        """初始化并注册默认支持的全格式解析器集群"""
        parsers: List[BaseParser] = [
            OFDParser(),
            XLSXParser(),
            MPPParser(),
            CADParser(),
            DOCXParser(),
            PPTXParser(),
            PDFParser(),
        ]
        for parser in parsers:
            self.register_parser(parser)

    def register_parser(self, parser: BaseParser) -> None:
        """注册解析器至内部路由字典"""
        for ext in parser.supported_extensions:
            norm_ext = ext.lower().strip()
            if not norm_ext.startswith("."):
                norm_ext = f".{norm_ext}"
            self._registry[norm_ext] = parser

    def get_parser_by_extension(self, extension: str) -> Optional[BaseParser]:
        """根据文件扩展名查询适配解析器"""
        norm_ext = extension.lower().strip()
        if not norm_ext.startswith("."):
            norm_ext = f".{norm_ext}"
        return self._registry.get(norm_ext)

    def sniff_format(self, content: bytes, file_name: str) -> Optional[str]:
        """
        基于文件头魔数 (Magic Bytes) 嗅探真实格式，防止文件扩展名被恶意篡改或缺失。
        """
        if not content or len(content) < 4:
            return None

        # 1. PDF 判定: %PDF- (0x25 0x50 0x44 0x46)
        if content.startswith(b"%PDF-"):
            return ".pdf"

        # 2. ZIP 归档家族判定: PK\x03\x04 (0x50 0x4B 0x03 0x04)
        if content.startswith(b"PK\x03\x04"):
            try:
                with zipfile.ZipFile(io.BytesIO(content), "r") as z:
                    names = z.namelist()
                    # OFD 国标公文: 根目录存在 OFD.xml
                    if any("OFD.xml" in n for n in names):
                        return ".ofd"
                    # Word: 存在 word/document.xml
                    if any(n.startswith("word/") for n in names):
                        return ".docx"
                    # Excel: 存在 xl/workbook.xml
                    if any(n.startswith("xl/") for n in names):
                        return ".xlsx"
                    # PPT: 存在 ppt/presentation.xml
                    if any(n.startswith("ppt/") for n in names):
                        return ".pptx"
            except Exception:
                pass  # 损坏的 zip 继续尝试其他探测

        # 3. Project XML (MSPDI) 判定
        header_sample = content[:1024].decode("utf-8", errors="ignore")
        if "<Project" in header_sample and ("http://schemas.microsoft.com/project" in header_sample or "<Tasks>" in header_sample):
            return ".xml"

        # 4. CAD DXF 判定: ASCII 结构包含 SECTION / HEADER
        cad_sample = content[:512].decode("latin-1", errors="ignore").strip()
        if cad_sample.startswith("0\nSECTION") or cad_sample.startswith("0\r\nSECTION") or "SECTION\n2\nHEADER" in cad_sample:
            return ".dxf"

        # 5. 回退使用原始文件后缀
        _, ext = os.path.splitext(file_name)
        if ext:
            return ext.lower()

        return None

    def get_parser(self, file_name: str, content: Optional[bytes] = None) -> BaseParser:
        """
        根据文件名或文件二进制嗅探获取最佳匹配的解析器。
        """
        # 1. 优先根据魔数嗅探
        sniffed_ext: Optional[str] = None
        if content:
            sniffed_ext = self.sniff_format(content, file_name)

        if sniffed_ext and sniffed_ext in self._registry:
            return self._registry[sniffed_ext]

        # 2. 其次根据原始文件名后缀匹配
        _, ext = os.path.splitext(file_name)
        norm_ext = ext.lower()
        if norm_ext in self._registry:
            return self._registry[norm_ext]

        raise UnsupportedFormatException(
            f"未找到支持文件 '{file_name}' (嗅探格式: {sniffed_ext or '未知'}) 的解析适配器。",
            file_name=file_name,
            details={"sniffed_ext": sniffed_ext, "registered_extensions": list(self._registry.keys())}
        )

    async def parse_document(
        self,
        content: bytes,
        file_name: str,
        tenant_id: str = "default",
        document_id: Optional[str] = None,
        **kwargs: Any,
    ) -> UnifiedDocumentAST:
        """
        对外统一门面调用入口。包含完整前置防御性检查与异常转换。
        """
        # 前置空内容防御
        if not content or len(content.strip()) == 0:
            raise EmptyDocumentError(
                f"文档 '{file_name}' 内容为空 (0 字节)，无法解析为 AST。",
                file_name=file_name
            )

        parser = self.get_parser(file_name=file_name, content=content)
        try:
            return await parser.parse(
                content=content,
                file_name=file_name,
                tenant_id=tenant_id,
                document_id=document_id,
                **kwargs
            )
        except ParserError:
            raise
        except Exception as e:
            raise MalformedDocumentError(
                f"解析文档 '{file_name}' 时遭遇不可恢复异常: {str(e)}",
                file_name=file_name,
                details={"original_exception": type(e).__name__}
            ) from e


# 全局单例工厂
parser_factory = ParserFactory()

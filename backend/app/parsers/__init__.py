"""
多源异构文档解析集群 (Heterogeneous Document Parsing Cluster)
支持国标公文 (OFD)、造价清单 (XLSX)、进度计划 (MPP)、CAD图纸 (DXF)、汇报演示 (PPTX)、工程标书 (DOCX)、双层/扫描PDF。
统一输出标准 UnifiedDocumentAST 语法树。
"""

from app.parsers.base import (
    BaseParser,
    EmptyDocumentError,
    MalformedDocumentError,
    ParserError,
    PasswordProtectedError,
    UnsupportedFormatException,
)
from app.parsers.factory import (
    ParserFactory,
    parser_factory,
)
from app.parsers.ofd_parser import OFDParser
from app.parsers.xlsx_parser import XLSXParser
from app.parsers.mpp_parser import MPPParser
from app.parsers.cad_parser import CADParser
from app.parsers.docx_parser import DOCXParser
from app.parsers.pptx_parser import PPTXParser
from app.parsers.pdf_parser import PDFParser

__all__ = [
    "BaseParser",
    "EmptyDocumentError",
    "MalformedDocumentError",
    "ParserError",
    "PasswordProtectedError",
    "UnsupportedFormatException",
    "ParserFactory",
    "parser_factory",
    "OFDParser",
    "XLSXParser",
    "MPPParser",
    "CADParser",
    "DOCXParser",
    "PPTXParser",
    "PDFParser",
]

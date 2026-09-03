"""
统一多源异构文档 AST 数据协议规范 (Unified Document AST Schema)
支持原生 DOCX、PDF/扫描件、OFD 国标公文、XLSX 预算清单、PPTX 汇报、MPP 进度网络图、CAD 图纸技术说明。
"""

from enum import Enum
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field


class DocumentSourceType(str, Enum):
    """支持的异构文档格式枚举"""
    DOCX = "docx"
    PDF = "pdf"
    OFD = "ofd"          # 中国国家版式电子公文标准
    XLSX = "xlsx"        # 电子表格 / 工程量清单 / 商务标
    PPTX = "pptx"        # 汇报答辩 PPT / 监管汇报
    MPP = "mpp"          # Microsoft Project 施工进度计划
    CAD = "cad"          # AutoCAD DXF/DWG 图纸文字与材料表
    IMAGE = "image"      # 扫描件 / 纸质凭据


class ASTBlockType(str, Enum):
    """文档统一 AST 块级元素类型"""
    HEADING = "heading"              # 章节标题（带 level）
    PARAGRAPH = "paragraph"          # 普通正文段落
    TABLE = "table"                  # 结构化表格
    SCHEDULE_TASK = "schedule_task"  # 进度计划任务（含起止时间、工期、前置依赖）
    CAD_NOTE = "cad_note"            # CAD 图纸设计说明 / 设备明细
    SPEAKER_NOTE = "speaker_note"    # PPT 演讲者备注 / 隐式设计意图
    CALLOUT = "callout"              # 警示框 / 关键批注 / 法律声明


class BoundingBox(BaseModel):
    """视觉定位包围盒坐标 (用于 PDF / OFD / 扫描件反查锚点)"""
    x0: float = Field(..., description="左上角 X")
    y0: float = Field(..., description="左上角 Y")
    x1: float = Field(..., description="右下角 X")
    y1: float = Field(..., description="右下角 Y")
    page_number: int = Field(default=1, description="对应页码")


class TableCell(BaseModel):
    """表格单元格元数据"""
    row: int
    col: int
    row_span: int = 1
    col_span: int = 1
    text: str
    is_header: bool = False


class TableData(BaseModel):
    """多级/合并表格结构化实体"""
    headers: List[List[str]] = Field(default_factory=list, description="多级表头矩阵")
    rows: List[List[str]] = Field(default_factory=list, description="二维单元格文本")
    cells: List[TableCell] = Field(default_factory=list, description="含合并单元格跨度的原始网格")
    markdown: str = Field(default="", description="表格的保真 Markdown 表现形式")
    summary: Optional[str] = Field(default=None, description="LLM/多视角生成的自然语言摘要，用于向量检索")


class ScheduleTaskData(BaseModel):
    """工程进度任务实体 (来源于 MPP 或进度表)"""
    task_id: str
    task_name: str
    duration_days: float = Field(..., description="工期日历天数")
    start_date: Optional[str] = None
    finish_date: Optional[str] = None
    is_critical_path: bool = False
    predecessors: List[str] = Field(default_factory=list, description="前置依赖任务ID")


class ASTNode(BaseModel):
    """统一 AST 节点定义"""
    block_id: str = Field(..., description="块全局唯一 ID")
    block_type: ASTBlockType
    level: Optional[int] = Field(default=None, description="标题大纲级别 (1~9)，非标题为 None")
    section_path: List[str] = Field(default_factory=list, description="层级大纲路径，如 ['第三章 施工组织', '3.2 工期计划']")
    text_content: str = Field(..., description="纯文本内容")
    
    # 针对特殊块的结构化载荷
    table_data: Optional[TableData] = None
    schedule_data: Optional[ScheduleTaskData] = None
    
    # 溯源与定位元数据
    page_or_sheet: Optional[str] = Field(default=None, description="页码 / Sheet 名 / Slide 编号")
    bbox: Optional[BoundingBox] = None
    extra_metadata: Dict[str, Any] = Field(default_factory=dict, description="额外原始属性")


class UnifiedDocumentAST(BaseModel):
    """统一文档抽象语法树 (Unified Document AST)"""
    document_id: str
    tenant_id: str
    file_name: str
    source_type: DocumentSourceType
    total_pages_or_sheets: int = 1
    nodes: List[ASTNode] = Field(default_factory=list, description="线性化或树状拓扑的 AST 节点列表")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="文档级全局元数据")

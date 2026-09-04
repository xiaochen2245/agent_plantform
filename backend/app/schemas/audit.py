"""
Pydantic v2 规范的数据契约模型库：文档大纲质检、排版格式质检、数值一致性与招投标偏离度报告
对齐 Phase 2 (M2) 核心特性 (Features 15-22) 及领域模型 (backend/app/models/audit_rag.py)
"""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union
from pydantic import BaseModel, Field, ConfigDict

from app.models.audit_rag import DeviationType, SeverityLevel, TaskStatus, TaskType
from app.schemas.ast import BoundingBox


# ===========================================================================
# 1. 大纲层级与排版格式质检协议 (Features 15 & 16)
# ===========================================================================

class OutlineIssueType(str, Enum):
    """大纲层级与序号异常枚举"""
    SEQUENCE_GAP = "sequence_gap"                      # 序号断层 (如 1.1 跳至 1.3 缺失 1.2)
    LEVEL_JUMP = "level_jump"                          # 标题层级跳级 (如 1 级跳至 3 级缺失 2 级)
    ROOT_LEVEL_SKIP = "root_level_skip"                # 根标题断层 (首个标题直接以 2 级或更高开头)
    DUPLICATE_NUMBER = "duplicate_number"              # 标题序号重复 (同层级出现两个相同编号)
    OUT_OF_ORDER = "out_of_order"                      # 标题序号倒序 (如 1.3 出现于 1.2 之前)
    HIERARCHY_PREFIX_MISMATCH = "prefix_mismatch"      # 子标题前缀与父标题不匹配 (如 第2章 下出现 1.1)
    CONVENTION_INCONSISTENCY = "convention_inconsistent"  # 同级标题编号体系混用
    EMPTY_HEADING_TITLE = "empty_heading_title"        # 标题文本内容为空


class FormatIssueType(str, Enum):
    """排版与表格格式异常枚举"""
    TABLE_EMPTY = "table_empty"                        # 表格无有效数据
    TABLE_EMPTY_CELL_RATIO_HIGH = "empty_cell_ratio_high"  # 空单元格比例超限
    TABLE_EMPTY_ROW = "table_empty_row"                # 数据全空行
    TABLE_EMPTY_COLUMN = "table_empty_column"          # 数据全空列
    TABLE_COLUMN_MISMATCH = "table_col_mismatch"       # 表格各行列数不对齐/网格残缺
    TABLE_UNMERGED_HEADER = "table_unmerged_header"    # 表头空白未合并异常
    TABLE_MISSING_HEADER = "table_missing_header"      # 表格缺少有效表头
    MISSING_TABLE_CAPTION = "missing_table_caption"    # 表格缺失编号与题注
    TABLE_CAPTION_SEQUENCE_GAP = "table_caption_gap"   # 表格题注编号断层 (表1-1 -> 表1-3)
    MISSING_FIGURE_CAPTION = "missing_figure_caption"  # 图件缺失题注
    FIGURE_CAPTION_SEQUENCE_GAP = "fig_caption_gap"    # 图件题注编号断层 (图1-1 -> 图1-3)
    ORPHAN_FIGURE_REFERENCE = "orphan_fig_ref"         # 正文引用了不存在的图件编号
    BROKEN_LIST_SEQUENCE = "broken_list_seq"           # 列表序号断层 ((1) -> (3))
    TRUNCATED_LIST_ITEM = "truncated_list_item"        # 列表项文本未完结或异常截断
    HANGING_LIST_MARKER = "hanging_list_marker"        # 悬挂列表项 (仅有标号无实质文本)


class NumberingFamily(str, Enum):
    """编号体系族系枚举"""
    DECIMAL_DOT = "decimal_dot"              # 多级点分十进制: 1.1, 1.1.1, 2.3.4
    CHINESE_CHAPTER = "chinese_chapter"      # 中文大写章/节: 第一章, 第2章, 第一节
    CHINESE_IDEOGRAPHIC = "chinese_ideo"     # 中文顿号序数: 一、, 二、, 三、
    CHINESE_PARENTHESIZED = "chinese_paren"  # 中文括号序数: （一）, (二)
    ARABIC_DOT = "arabic_dot"                # 阿拉伯单数点: 1., 2., 1、, 2、
    ARABIC_PARENTHESIZED = "arabic_paren"    # 阿拉伯数字括号: （1）, (2)
    CIRCLED = "circled"                      # 带圈数字: ①, ②, ③
    ROMAN = "roman"                          # 罗马数字: I., II., III., IV.
    ALPHABETIC = "alphabetic"                # 英文字母编号: A., B., a., b.
    UNNUMBERED = "unnumbered"                # 无标号标题


class OutlineValidatorConfig(BaseModel):
    """质检规则配置参数"""
    model_config = ConfigDict(extra="ignore")
    max_empty_cell_ratio: float = Field(default=0.30, ge=0.0, le=1.0, description="表格空单元格比例告警阈值")
    max_heading_level_jump: int = Field(default=1, ge=1, le=5, description="允许的最大标题跃升幅度，默认1")
    allow_root_level_skip: bool = Field(default=False, description="是否允许文档以非1级标题开头")
    table_caption_required: bool = Field(default=True, description="是否强制要求表格配备题注")
    figure_caption_required: bool = Field(default=True, description="是否强制要求图件配备题注")
    check_broken_lists: bool = Field(default=True, description="是否检查正文列表序号断层")
    check_caption_continuity: bool = Field(default=True, description="是否校验图表题注编号连续性")
    strict_prefix_matching: bool = Field(default=True, description="点分标题是否强制校验与父级前缀一致性")


class OutlineIssue(BaseModel):
    """大纲层级与序号问题诊断结果项"""
    model_config = ConfigDict(extra="ignore")
    issue_id: str = Field(default_factory=lambda: f"iss_out_{uuid.uuid4().hex[:8]}")
    issue_type: OutlineIssueType
    severity: SeverityLevel
    node_id: str = Field(..., description="触发该问题的 ASTNode block_id")
    section_path: List[str] = Field(default_factory=list, description="所属章节路径面包屑")
    current_heading: str = Field(..., description="当前标题文本")
    current_level: Optional[int] = Field(default=None, description="当前标题级别 (1~9)")
    expected_level: Optional[int] = Field(default=None, description="预期标题级别")
    expected_heading: Optional[str] = Field(default=None, description="预期连续序号标题")
    missing_items: List[str] = Field(default_factory=list, description="缺失的序号项列表，如 ['1.2']")
    page_or_sheet: Optional[str] = Field(default=None, description="页码/Sheet/Slide定位")
    bbox: Optional[BoundingBox] = Field(default=None, description="视觉定位包围盒")
    message: str = Field(..., description="精确诊断信息")
    suggestion: str = Field(..., description="智能补正与修正建议")


class FormatIssue(BaseModel):
    """排版与表格格式问题诊断结果项"""
    model_config = ConfigDict(extra="ignore")
    issue_id: str = Field(default_factory=lambda: f"iss_fmt_{uuid.uuid4().hex[:8]}")
    issue_type: FormatIssueType
    severity: SeverityLevel
    node_id: str = Field(..., description="触发该问题的 ASTNode block_id")
    section_path: List[str] = Field(default_factory=list, description="所属章节路径")
    page_or_sheet: Optional[str] = Field(default=None, description="页码/Sheet定位")
    bbox: Optional[BoundingBox] = Field(default=None, description="视觉定位包围盒")
    metric_name: Optional[str] = Field(default=None, description="度量指标名称")
    metric_value: Optional[float] = Field(default=None, description="实际度量值")
    threshold: Optional[float] = Field(default=None, description="阈值设定")
    details: Dict[str, Any] = Field(default_factory=dict, description="结构化详情载荷")
    message: str = Field(..., description="精确诊断信息")
    suggestion: str = Field(..., description="智能补正与修正建议")


class OutlineValidationReport(BaseModel):
    """大纲层级质检报告"""
    model_config = ConfigDict(extra="ignore")
    document_id: str
    total_headings_inspected: int = 0
    is_valid: bool = True
    max_heading_level: int = 0
    numbering_conventions_detected: List[str] = Field(default_factory=list)
    issues: List[OutlineIssue] = Field(default_factory=list)
    issue_count_by_severity: Dict[str, int] = Field(default_factory=dict)
    summary: str = ""


class FormatValidationReport(BaseModel):
    """排版格式与表格质检报告"""
    model_config = ConfigDict(extra="ignore")
    document_id: str
    total_tables_inspected: int = 0
    total_paragraphs_inspected: int = 0
    is_valid: bool = True
    issues: List[FormatIssue] = Field(default_factory=list)
    issue_count_by_severity: Dict[str, int] = Field(default_factory=dict)
    table_stats: Dict[str, Any] = Field(default_factory=dict)
    list_stats: Dict[str, Any] = Field(default_factory=dict)
    summary: str = ""


class DocumentQualityReport(BaseModel):
    """统一文档综合质检总报告"""
    model_config = ConfigDict(extra="ignore")
    document_id: str
    file_name: str
    tenant_id: str
    overall_score: float = Field(default=100.0, ge=0.0, le=100.0)
    passed: bool = True
    total_issues_count: int = 0
    high_risk_count: int = 0
    outline_report: OutlineValidationReport
    format_report: FormatValidationReport
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ===========================================================================
# 2. 长文档跨章节数值一致性协议 (Features 17, 18, 19)
# ===========================================================================

class ConflictType(str, Enum):
    """一致性冲突类别"""
    NUMERICAL_MISMATCH = "numerical_mismatch"          # 绝对数值冲突 (如 450天 vs 360天)
    UNIT_INCOMPATIBLE = "unit_incompatible"            # 量纲冲突/换算异常
    TIMELINE_INCONSISTENCY = "timeline_inconsistency"  # 节点时间倒挂
    TOLERANCE_EXCEEDED = "tolerance_exceeded"          # 超出工程设计公差


class IssueSeverity(str, Enum):
    """一致性问题严重级别 (与 SeverityLevel 镜像兼容)"""
    CRITICAL = "critical"  # 致命风险 / 废标红线 (工期、总投资、重大参数背离)
    HIGH = "high"          # 高风险指标矛盾 (核心设备参数偏差、建筑面积不符)
    MEDIUM = "medium"      # 中度偏差 (辅助参数、子项预算不一致)
    LOW = "low"            # 轻微笔误/工程可接受舍入差


class MetricDimension(str, Enum):
    """指标所属大类维度"""
    DURATION = "工期"
    COST = "造价"
    AREA = "建筑面积"
    COP = "COP"
    COOLING_CAPACITY = "制冷量"
    AIR_FLOW = "风量"
    POWER = "功率"
    PUMP_HEAD = "扬程"
    GENERAL = "通用"


class StatementAnchor(BaseModel):
    """单处陈述的完整事实证据链锚点"""
    model_config = ConfigDict(extra="ignore")
    section_title: str = Field(..., description="所属章节路径，如 '第1章 投标总函'")
    page_or_sheet: str = Field(default="P.1", description="物理页码或工作表名称")
    raw_text: str = Field(..., description="原始匹配词片段，如 '总工期450日历天'")
    full_sentence: str = Field(..., description="上下文完整句子")
    raw_number: float = Field(..., description="提取出的原始数字")
    raw_unit: str = Field(default="", description="原始量纲单位")
    normalized_value: float = Field(..., description="归一化后的标准标量值")
    standard_unit: str = Field(..., description="标准量纲单位")
    block_id: Optional[str] = Field(default=None, description="所属 AST Node block_id")
    extra_context: Dict[str, Any] = Field(default_factory=dict, description="额外定位坐标或表格行列属性")


class ConsistencyConflict(BaseModel):
    """
    跨章节一致性冲突明细报告实体
    同时支持扁平化字段 (DISPATCH 要求) 与嵌套锚点结构 (旧版测试兼容)
    """
    model_config = ConfigDict(extra="ignore")
    conflict_id: str = Field(..., description="冲突唯一编号，如 CONF-0001")
    metric_category: str = Field(..., description="指标分类 (工期/造价/设备参数等)")
    metric_name: str = Field(..., description="指标规范名称，如 '施工总工期'")
    dimension: str = Field(default="", description="指标维度")
    conflict_type: ConflictType = Field(default=ConflictType.NUMERICAL_MISMATCH)
    severity: IssueSeverity = Field(..., description="风险等级")

    # 扁平化证据链 (满足 DISPATCH.md 要求)
    value_a: float = Field(..., description="基准陈述归一化数值")
    unit_a: str = Field(..., description="基准陈述标准单位")
    section_a: str = Field(..., description="基准陈述章节路径")
    quote_a: str = Field(..., description="基准陈述原文摘录")
    page_a: str = Field(..., description="基准陈述页码")

    value_b: float = Field(..., description="矛盾陈述归一化数值")
    unit_b: str = Field(..., description="矛盾陈述标准单位")
    section_b: str = Field(..., description="矛盾陈述章节路径")
    quote_b: str = Field(..., description="矛盾陈述原文摘录")
    page_b: str = Field(..., description="矛盾陈述页码")

    diff_value: float = Field(..., description="绝对差值 abs(val_a - val_b)")
    diff_percent: float = Field(..., description="百分比差值 abs(val_a - val_b) / max(val_a, val_b) * 100%")

    # 兼容字段
    difference_value: float = Field(..., description="别名: 绝对差值")
    difference_percentage: float = Field(..., description="别名: 百分比差值")

    # 嵌套结构证据锚点 (满足 Phase 1 test_audit_rag_workflow.py 断言)
    baseline_statement: StatementAnchor = Field(..., description="前文基准陈述")
    conflicting_statement: StatementAnchor = Field(..., description="后文矛盾陈述")

    # 深度审查意见
    detailed_reason: str = Field(..., description="矛盾原因分析与法律/废标技术风险阐明")
    correction_suggestion: str = Field(..., description="推荐的修正方案与统一表述建议")


class ConsistencyReport(BaseModel):
    """长文档跨章节数据一致性总报告"""
    model_config = ConfigDict(extra="ignore")
    document_title: str
    total_metrics_scanned: int
    conflicts_found: int
    critical_count: int
    high_count: int
    medium_count: int = 0
    low_count: int = 0
    conflicts: List[ConsistencyConflict] = Field(default_factory=list)
    extracted_knowledge_graph: Dict[str, List[Dict[str, Any]]] = Field(
        default_factory=dict, description="按指标归类的全量陈述锚点图谱"
    )
    scanned_dimensions: List[str] = Field(default_factory=list, description="本次扫描涉及的指标维度")


# ===========================================================================
# 3. 招投标评分表拆解与 4 类偏离度协议 (Features 20, 21, 22)
# ===========================================================================

class ScoringCategory(str, Enum):
    """评分大项类别"""
    TECHNICAL = "technical"          # 技术标 (施工方案、质量、进度、安全文明)
    COMMERCIAL = "commercial"        # 商务标 (报价构成、付款方式、财务状况)
    QUALIFICATION = "qualification"  # 资质资信 (企业资质、项目团队、获奖业绩)
    PRICE = "price"                  # 价格评分项 (基准价偏离度打分)
    GENERAL = "general"              # 综合标 / 其他评审项


class MetricDirection(str, Enum):
    """数值与指标优选方向"""
    HIGHER_BETTER = "higher_better"    # 越大越优 (如 COP、能效比、质保期、业绩数)
    LOWER_BETTER = "lower_better"      # 越小越优 (如 施工工期、项目造价、能耗指标)
    EXACT_EQUAL = "exact_equal"        # 严格相等 (如 规定品牌清单、指定规程标准)
    BOOLEAN_QUALIFIED = "boolean"      # 是非达标型 (如 是否具备 ISO 证书、安全生产许可证)
    QUALITATIVE = "qualitative"        # 定性专家打分型 (如 施工组织合理性、应急预案可操作性)


class CriteriaConstraint(BaseModel):
    """评分项中解析出的结构化参数约束"""
    model_config = ConfigDict(extra="ignore")
    metric_name: str = Field(..., description="提取出的指标名称，如 '工期', 'COP', '质保期'")
    target_value: float = Field(..., description="基准指标要求值")
    target_unit: str = Field(default="", description="指标量纲单位，如 '天', '年', 'kW'")
    operator: str = Field(default="<=", description="比较逻辑运算符: '<=', '>=', '=='")
    direction: MetricDirection = Field(default=MetricDirection.LOWER_BETTER)


class TenderScoringItem(BaseModel):
    """单个评分指标实体 (Feature 20)"""
    model_config = ConfigDict(extra="ignore")
    criteria_id: str = Field(..., description="评分项唯一标识，如 'CRIT_TECH_01'")
    category: ScoringCategory = Field(default=ScoringCategory.TECHNICAL)
    parent_category_name: str = Field(default="", description="父级大项名称，如 '一、施工组织设计'")
    name: str = Field(..., description="评分指标名称，如 '施工总工期与进度控制措施'")
    max_score: float = Field(..., description="本项满分分值")
    min_score: float = Field(default=0.0, description="本项最低得分")
    is_mandatory: bool = Field(default=False, description="是否为带 ★/* 强制性或废标条款")
    scoring_guide: str = Field(..., description="招标文件载明的详细评分细则与扣分标准")
    constraint: Optional[CriteriaConstraint] = Field(default=None, description="数值硬性约束")
    keywords: List[str] = Field(default_factory=list, description="本项关联的高价值检索关键词")
    source_node_id: str = Field(default="", description="在招标文件 AST 中的 Block ID")
    source_page: Optional[int] = Field(default=None, description="招标文件中的物理页码")
    source_section: str = Field(default="", description="招标文件所在大纲章节路径")


class TenderScoringTable(BaseModel):
    """招标文件综合评分标准体系表 (Feature 20 产物)"""
    model_config = ConfigDict(extra="ignore")
    document_id: str
    tenant_id: str
    title: str = Field(default="招标文件评标办法与评分标准")
    total_max_score: float = Field(default=100.0, description="评分表总分，通常为 100 分")
    items: List[TenderScoringItem] = Field(default_factory=list)
    raw_table_node_ids: List[str] = Field(default_factory=list, description="来源表格节点ID")


class AlignmentCandidate(BaseModel):
    """自编标书中的语义匹配候选响应 (Feature 21 产物)"""
    model_config = ConfigDict(extra="ignore")
    criteria_id: str
    is_matched: bool = Field(default=False, description="是否在标书中检索到对应响应")
    alignment_score: float = Field(default=0.0, description="综合对齐置信得分 [0.0 ~ 1.0]")
    node_id: str = Field(default="", description="匹配到的标书 AST 节点 ID")
    section_path: str = Field(default="", description="标书大纲章节路径")
    page_number: Optional[int] = Field(default=None, description="标书物理页码")
    matched_quote: str = Field(default="", description="标书中最核心的响应原句或表格片段")
    full_section_content: str = Field(default="", description="该小节的完整段落上下文")


class DeviationEvaluationResult(BaseModel):
    """偏离度综合判定明细 (Feature 22 产物，严格兼容 ReviewResult)"""
    model_config = ConfigDict(extra="ignore")
    criteria_id: str
    deviation_type: DeviationType
    severity: SeverityLevel
    confidence: float = Field(..., ge=0.0, le=1.0, description="判定置信度")
    score_assigned: float = Field(..., description="预估拟得分值")
    max_score: float = Field(..., description="该项最高满分分值")

    # 标书响应证据锚点
    source_section: str = Field(default="", description="自编标书章节")
    source_page: Optional[int] = Field(default=None, description="自编标书页码")
    source_quote: str = Field(default="", description="自编标书原文引述")

    # 招标文件基准锚点
    benchmark_section: str = Field(default="", description="招标文件评分项章节")
    benchmark_quote: str = Field(default="", description="招标文件要求原文")

    # 诊断与改进意见
    title: str = Field(..., description="评分项名称")
    description: str = Field(..., description="差异分析与偏离理由")
    suggestion: str = Field(default="", description="纠偏补正或优势展示建议")

    # 结构化对比载荷
    diff_payload: Dict[str, Any] = Field(default_factory=dict)


class TenderAlignmentReport(BaseModel):
    """招投标对齐与偏离度全局综合报告"""
    model_config = ConfigDict(extra="ignore")
    tenant_id: str
    source_document_id: str = Field(..., description="自编标书 ID")
    target_document_id: str = Field(..., description="招标文件 ID")
    total_criteria_count: int
    full_compliance_count: int
    positive_count: int
    negative_count: int
    missing_count: int
    total_max_score: float
    total_estimated_score: float
    compliance_rate: float = Field(..., description="合规率 (%) = (完全满足+正偏离) / 总项数 * 100")
    critical_kill_items: List[DeviationEvaluationResult] = Field(
        default_factory=list, description="触发废标风险的重大负偏离或缺失项"
    )
    results: List[DeviationEvaluationResult] = Field(default_factory=list)


# ===========================================================================
# 4. 审查请求与数据库映射数据契约 (ReviewResult / AuditTask)
# ===========================================================================

class ReviewResultSchema(BaseModel):
    """ReviewResult 序列化传输协议"""
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    task_id: str
    deviation_type: DeviationType
    severity: SeverityLevel
    confidence: float
    rule_category: str
    title: str
    description: str
    suggestion: str
    source_section: str
    source_page: Optional[int] = None
    source_quote: str
    benchmark_section: str
    benchmark_quote: str
    diff_payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None


class AuditTaskCreate(BaseModel):
    """发起审查任务请求"""
    model_config = ConfigDict(extra="ignore")
    task_type: TaskType
    document_id: str
    benchmark_document_id: Optional[str] = None
    title: Optional[str] = None
    config: Dict[str, Any] = Field(default_factory=dict)


class AuditTaskResponse(BaseModel):
    """审查任务状态响应"""
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    task_type: TaskType
    status: TaskStatus
    document_id: str
    benchmark_document_id: Optional[str] = None
    title: str
    error_message: Optional[str] = None
    summary_report: Optional[Dict[str, Any]] = None
    total_issues: int = 0
    critical_issues: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

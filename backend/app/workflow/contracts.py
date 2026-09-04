"""
LangGraph 双智能体状态机强类型数据契约与 Pydantic 规范
对齐 Phase 3 (M3) 特性 (Features 23-30) 与 models/audit_rag.py
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, TypedDict, Union
from pydantic import BaseModel, ConfigDict, Field

from app.models.audit_rag import DeviationType, SeverityLevel, TaskStatus, TaskType


# ---------------------------------------------------------------------------
# 1. LangGraph StateGraph TypedDict 契约定义
# ---------------------------------------------------------------------------

class PatchDiffItem(TypedDict, total=False):
    """
    行内批注与靶向手术式修订差异项 (Feature 25)
    包含定位、错误原文、建议替换合规文本与严重级别
    """
    issue_id: str                      # 缺陷唯一标识，如 "iss_sched_0_01"
    target_section: str                # 所属大纲章节定位，如 "第2章 施工总工期规划与进度保障措施"
    error_quote: str                   # 违规/矛盾的原文切片
    suggested_replacement: str         # 建议替换的合规修正文本
    reason: str                        # 判定缺陷或负偏离的原因说明
    severity: SeverityLevel            # 严重级别: CRITICAL, HIGH, MEDIUM, LOW, INFO

    # 兼容字段
    location: str
    original_text: str
    suggested_patch: str
    issue: str


class AuditFeedback(TypedDict):
    """
    校核智能体 (Critic Agent) 输出的结构化审查报告 (Feature 24)
    """
    passed: bool                       # 是否通过全部核验 (无 CRITICAL 且综合评分 >= 85.0)
    score: float                       # 质量综合评分 [0.0 ~ 100.0]
    hallucination_detected: bool       # 是否检出事实性幻觉或数值矛盾
    issues: List[PatchDiffItem]        # 结构化 Patch Diff 差异项列表
    summary_comment: str               # 综合审查评审结论与指导意见


class GraphState(TypedDict, total=False):
    """
    LangGraph 双智能体反思状态机全局状态字典 (Features 23, 26, 27)
    包含租户边界、任务上下文、当前草稿、审查反馈与历史审计链路
    """
    tenant_id: str                     # 租户唯一编码 (多租户隔离硬边界)
    task_id: str                       # 业务审查任务 ID (对齐 AuditTask.id)
    thread_id: str                     # LangGraph 线程会话 ID (持久化追踪主键)
    rfp_requirements: str              # 招标文件需求 / 用户指令 / 评分要求
    context_chunks: List[Dict[str, Any]] # RAG 召回的 Parent Chunks 事实依据与引用锚点
    risk_guardrails: Optional[Union[List[str], str]] # 历史经验与风险主动预警注入项 (来自 M3_2)

    draft: str                         # 当前版本方案草稿
    audit_feedback: Optional[AuditFeedback] # 最近一轮校核审查报告
    iteration_count: int               # 已完成的反思重写轮次 (初始为 0)
    max_iterations: int                # 允许的最大重写轮次 (硬性上限为 2)

    status: TaskStatus                 # 当前状态 (PENDING, PROCESSING, SUCCESS, HUMAN_REVIEW, FAILED)
    review_history: List[Dict[str, Any]] # 全生命周期版本演进与审计追踪记录
    human_patch: Optional[str]         # HITL 人工介入修正的补丁内容
    llm_config: Optional[Dict[str, Any]] # 大模型运行参数 (temperature, model 等)


# ---------------------------------------------------------------------------
# 2. Pydantic 对称契约模型 (供 REST API、序列化与校验使用)
# ---------------------------------------------------------------------------

class PatchDiffItemSchema(BaseModel):
    """差异项 Pydantic 校验模型"""
    model_config = ConfigDict(extra="ignore")

    issue_id: str = Field(..., description="缺陷唯一ID")
    target_section: str = Field(..., description="目标章节定位")
    error_quote: str = Field(..., description="存在矛盾的原文")
    suggested_replacement: str = Field(..., description="合规替换建议")
    reason: str = Field(..., description="偏差原因与违规说明")
    severity: SeverityLevel = Field(default=SeverityLevel.HIGH, description="严重级别")


class AuditFeedbackSchema(BaseModel):
    """审查报告 Pydantic 校验模型"""
    model_config = ConfigDict(extra="ignore")

    passed: bool = Field(..., description="是否通过审查")
    score: float = Field(..., ge=0.0, le=100.0, description="综合质量得分")
    hallucination_detected: bool = Field(default=False, description="是否检出幻觉")
    issues: List[PatchDiffItemSchema] = Field(default_factory=list, description="缺陷列表")
    summary_comment: str = Field(default="", description="审查综合结论")


class GraphStateSchema(BaseModel):
    """全局图状态 Pydantic 校验模型"""
    model_config = ConfigDict(extra="ignore")

    tenant_id: str = Field(..., description="租户标识")
    task_id: str = Field(..., description="任务标识")
    thread_id: str = Field(..., description="会话线程标识")
    rfp_requirements: str = Field(default="", description="招标文件需求")
    context_chunks: List[Dict[str, Any]] = Field(default_factory=list)
    risk_guardrails: Optional[Union[List[str], str]] = None
    draft: str = Field(default="")
    audit_feedback: Optional[AuditFeedbackSchema] = None
    iteration_count: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=2, le=2)
    status: TaskStatus = Field(default=TaskStatus.PROCESSING)
    review_history: List[Dict[str, Any]] = Field(default_factory=list)
    human_patch: Optional[str] = None
    llm_config: Optional[Dict[str, Any]] = None


class HumanReviewPayload(BaseModel):
    """HITL 恢复执行请求入参 (Feature 28)"""
    model_config = ConfigDict(extra="ignore")

    thread_id: str = Field(..., description="待恢复的会话线程ID")
    human_patch: Optional[str] = Field(None, description="人工纠偏文本/特批替换内容")
    decision: Literal["approve", "override_and_finish", "reject"] = Field(
        default="override_and_finish", description="人工判定结论"
    )


# ---------------------------------------------------------------------------
# 3. 历史工程经验风险与主动拦截契约 (Features 29 & 30)
# ---------------------------------------------------------------------------

class ProjectCharter(BaseModel):
    """
    新项目立项任务书 / 拟定参数契约
    用于触发前置主动风险拦截器
    """
    model_config = ConfigDict(extra="ignore")

    project_name: str = Field(..., description="工程项目全称")
    project_type: str = Field(..., description="工程类别: 房建 / 市政 / 弱电智能化 / 轨道交通 / 水利水电 / 公路桥梁")
    scale_description: str = Field(..., description="项目建设规模简述 (如: 总建筑面积12万㎡，地下3层，开挖深度8米)")
    duration_days: Optional[int] = Field(None, description="计划工期总日历天数")
    budget_cny_ten_thousand: Optional[float] = Field(None, description="项目概算/投标报价 (万元)")
    excavation_depth_meters: Optional[float] = Field(None, description="基坑开挖深度 (米)")
    special_conditions: List[str] = Field(
        default_factory=list,
        description="特殊工况标签: ['雨季施工', '富水地层', '临近既有地铁线', '装配式建筑', '危大工程']"
    )
    charter_text: Optional[str] = Field(None, description="立项任务书全文或招标文件关键技术条款摘录")

    def to_embedding_text(self) -> str:
        """格式化为稠密向量检索查询文本"""
        parts = [
            f"工程类别：{self.project_type}",
            f"工程名称：{self.project_name}",
            f"建设规模：{self.scale_description}",
        ]
        if self.duration_days is not None:
            parts.append(f"工期承诺：{self.duration_days}日历天")
        if self.budget_cny_ten_thousand is not None:
            parts.append(f"投资造价预算：{self.budget_cny_ten_thousand}万元")
        if self.excavation_depth_meters is not None:
            parts.append(f"基坑开挖深度：{self.excavation_depth_meters}米")
        if self.special_conditions:
            parts.append(f"特殊工况特征：{', '.join(self.special_conditions)}")
        if self.charter_text:
            parts.append(f"任务书补充说明：{self.charter_text[:500]}")
        return "；".join(parts)


class RiskWarningItem(BaseModel):
    """单项风险拦截预警条目 (Feature 30)"""
    model_config = ConfigDict(extra="ignore")

    warning_id: str = Field(..., description="预警唯一编号，如 'WRN-001'")
    risk_id: str = Field(..., description="命中的历史风险库记录 ID")
    risk_title: str = Field(..., description="风险标题")
    risk_category: str = Field(..., description="风险类别 (工期延误/造价超概/安全基坑坍塌/环保违规/资质造假)")
    severity: SeverityLevel = Field(..., description="风险等级 (CRITICAL, HIGH, MEDIUM, LOW)")
    matched_confidence: float = Field(..., ge=0.0, le=1.0, description="综合匹配置信度 [0.0 ~ 1.0]")
    match_reasons: List[str] = Field(..., description="命中判定依据列表")
    historical_case_reference: Dict[str, Any] = Field(
        default_factory=dict,
        description="关联的历史案卷回溯数据"
    )
    preventive_guardrail: str = Field(..., description="预防性设计与施工护栏要求 (直接注入生成智能体)")


class RiskInterceptionReport(BaseModel):
    """主动风险拦截总报告 (Feature 30)"""
    model_config = ConfigDict(extra="ignore")

    report_id: str = Field(..., description="报告唯一编号")
    tenant_id: str = Field(..., description="租户标识")
    project_name: str = Field(..., description="工程名称")
    project_type: str = Field(..., description="工程大类")
    risk_level: SeverityLevel = Field(default=SeverityLevel.LOW, description="项目整体风险最高等级")
    total_risks_matched: int = 0
    critical_count: int = 0
    high_count: int = 0
    warnings: List[RiskWarningItem] = Field(default_factory=list)
    guardrail_system_prompt_snippet: str = Field(
        default="", description="已格式化好的 Markdown 防护栏提示词片段，可直接拼接入 Generator System Prompt"
    )
    executive_summary: str = Field(default="", description="高管风险摘要与管理合规建议")
    intercepted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class HistoricalAuditRiskCreate(BaseModel):
    """创建历史经验风险库条目请求"""
    model_config = ConfigDict(extra="ignore")

    project_type: str
    risk_category: str
    risk_title: str
    severity: SeverityLevel = SeverityLevel.HIGH
    defect_description: str
    lesson_learned: str
    preventive_guardrail_prompt: str
    tags: List[str] = Field(default_factory=list)
    rule_conditions: Dict[str, Any] = Field(default_factory=dict)
    source_case_id: Optional[str] = None
    source_project_name: Optional[str] = None
    financial_loss_cny: Optional[float] = None
    delay_days: Optional[int] = None


class HistoricalAuditRiskResponse(HistoricalAuditRiskCreate):
    """历史经验风险库查询响应"""
    id: str
    tenant_id: str
    has_embedding: bool = False
    created_at: datetime
    updated_at: datetime

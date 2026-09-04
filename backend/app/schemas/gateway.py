"""
FastAPI 网关核心 Pydantic v2 请求与响应数据契约 (Gateway Schemas)
覆盖:
1. 文档多源上传与异步解析状态
2. 排版格式质检与招投标评分项对齐
3. 双智能体工作流执行、状态追踪、HITL 恢复与流式事件
4. 历史经验知识库与主动风险拦截
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field

from app.models.audit_rag import SeverityLevel, TaskStatus, TaskType
from app.schemas.audit import (
    DocumentQualityReport,
    OutlineValidatorConfig,
    TenderAlignmentReport,
)
from app.workflow.contracts import (
    AuditFeedbackSchema,
    HistoricalAuditRiskCreate,
    HistoricalAuditRiskResponse,
    HumanReviewPayload,
    PatchDiffItemSchema,
    ProjectCharter,
    RiskInterceptionReport,
    RiskWarningItem,
)


# ============================================================================
# 1. 文档服务契约 (Documents Gateway Schemas)
# ============================================================================

class DocumentUploadResponse(BaseModel):
    """文档上传响应契约"""
    model_config = ConfigDict(extra="ignore")

    document_id: str = Field(..., description="文档唯一标识 ID")
    task_id: str = Field(..., description="异步解析任务 Task ID")
    file_name: str = Field(..., description="原始文件名")
    file_type: str = Field(..., description="文档格式后缀 (pdf/docx/xlsx/mpp/cad/ofd/pptx)")
    file_size_bytes: int = Field(..., description="文件物理大小 (字节)")
    parse_status: str = Field(..., description="初始解析状态 (pending/parsing/success/failed)")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="创建时间")


class DocumentStatusResponse(BaseModel):
    """文档解析进度与切片统计查询契约"""
    model_config = ConfigDict(extra="ignore")

    document_id: str = Field(..., description="文档唯一标识 ID")
    tenant_id: str = Field(..., description="租户标识")
    file_name: str = Field(..., description="原始文件名")
    file_type: str = Field(..., description="文档格式后缀")
    file_size_bytes: int = Field(..., description="文件大小 (字节)")
    parse_status: str = Field(..., description="解析状态 (pending/parsing/success/failed)")
    total_chunks: int = Field(default=0, description="父子切片总数")
    ast_node_count: int = Field(default=0, description="解析产出的 AST 语法树节点总数")
    created_at: datetime = Field(..., description="上传时间")
    updated_at: Optional[datetime] = Field(None, description="最后更新时间")
    error_message: Optional[str] = Field(None, description="解析失败错误信息")


class DocumentListResponse(BaseModel):
    """文档列表查询契约"""
    model_config = ConfigDict(extra="ignore")

    total: int = Field(..., description="符合条件的文档总数")
    items: List[DocumentStatusResponse] = Field(default_factory=list, description="文档列表")


# ============================================================================
# 2. 质检与对齐契约 (Quality & Alignment Gateway Schemas)
# ============================================================================

class DocumentQualityCheckRequest(BaseModel):
    """排版与大纲断层质检请求"""
    model_config = ConfigDict(extra="ignore")

    document_id: str = Field(..., description="待质检的文档 ID")
    config: Optional[OutlineValidatorConfig] = Field(None, description="质检规则配置项")


class TenderAlignmentRequest(BaseModel):
    """自编标书 vs 招标文件评分表对齐请求"""
    model_config = ConfigDict(extra="ignore")

    source_document_id: str = Field(..., description="自编投标文件或技术标 Document ID")
    target_document_id: str = Field(..., description="招标文件评分标准 Document ID")
    config: Optional[Dict[str, Any]] = Field(None, description="高级对齐配置")


# ============================================================================
# 3. 工作流服务契约 (Dual-Agent Workflow Gateway Schemas)
# ============================================================================

class WorkflowRunRequest(BaseModel):
    """双智能体闭环方案生成与校核运行请求"""
    model_config = ConfigDict(extra="ignore")

    rfp_requirements: str = Field(..., description="招标文件技术条款与立项核心指标要求")
    context_chunks: Optional[List[Dict[str, Any]]] = Field(None, description="外部注入的 RAG 检索上下文切片")
    project_charter: Optional[ProjectCharter] = Field(None, description="立项任务书，提供时将自动触发历史经验风险拦截并注入防护栏")
    max_iterations: int = Field(default=2, ge=1, le=2, description="最大反思重写轮次 (严格 <= 2 触发熔断)")
    async_mode: bool = Field(default=False, description="是否以 Celery 异步后台任务方式执行")
    thread_id: Optional[str] = Field(None, description="工作流线程标识 (若未提供则系统自动生成)")


class WorkflowRunResponse(BaseModel):
    """工作流运行初次响应"""
    model_config = ConfigDict(extra="ignore")

    task_id: str = Field(..., description="审计任务 AuditTask ID")
    thread_id: str = Field(..., description="状态机 Checkpointer 线程 ID")
    tenant_id: str = Field(..., description="租户标识")
    status: TaskStatus = Field(..., description="任务当前状态 (processing/success/failed/human_review)")
    is_async: bool = Field(..., description="是否异步执行")
    draft: Optional[str] = Field(None, description="同步模式下输出的方案文本")
    audit_feedback: Optional[AuditFeedbackSchema] = Field(None, description="同步模式下 Critic 的审查反馈")
    iteration_count: int = Field(default=0, description="当前反思迭代轮次")
    max_iterations: int = Field(default=2, description="最大允许迭代轮次")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class WorkflowStateResponse(BaseModel):
    """工作流运行时状态与审计快照查询响应"""
    model_config = ConfigDict(extra="ignore")

    task_id: str = Field(..., description="审计任务 ID")
    thread_id: str = Field(..., description="工作流线程 ID")
    tenant_id: str = Field(..., description="租户标识")
    status: TaskStatus = Field(..., description="任务当前状态")
    draft: Optional[str] = Field(None, description="当前草案方案文本")
    audit_feedback: Optional[AuditFeedbackSchema] = Field(None, description="最新 Critic 审查评定")
    iteration_count: int = Field(default=0, description="已完成迭代轮次")
    review_history: List[Dict[str, Any]] = Field(default_factory=list, description="多轮审计历史追踪轨迹")
    human_patch: Optional[str] = Field(None, description="人工注入的纠偏补丁")
    updated_at: Optional[datetime] = Field(None, description="最后更新时间")


class WorkflowResumeRequest(BaseModel):
    """HITL 人工干预断点恢复请求"""
    model_config = ConfigDict(extra="ignore")

    decision: Literal["approve", "override_and_finish", "reject"] = Field(
        ..., description="人工决策: approve (批准通过), override_and_finish (特批覆盖并完成), reject (驳回失败)"
    )
    human_patch: Optional[str] = Field(None, description="人工专家修订条款或纠偏补充说明")
    auditor_name: Optional[str] = Field("expert_auditor", description="审核人身份签名")
    comments: Optional[str] = Field(None, description="人工审核意见批注")


class WorkflowResumeResponse(BaseModel):
    """HITL 恢复执行响应"""
    model_config = ConfigDict(extra="ignore")

    task_id: str = Field(..., description="审计任务 ID")
    thread_id: str = Field(..., description="工作流线程 ID")
    status: TaskStatus = Field(..., description="流转后的终态 (SUCCESS 或 FAILED)")
    final_draft: Optional[str] = Field(None, description="最终采纳的方案文本")
    decision: str = Field(..., description="人工决策操作")
    resumed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ============================================================================
# 4. 风险拦截契约 (Historical Risk Gateway Schemas)
# ============================================================================

class RiskSeedResponse(BaseModel):
    """历史工程经验种子播种响应"""
    model_config = ConfigDict(extra="ignore")

    tenant_id: str
    seeded_count: int
    message: str


class RiskListResponse(BaseModel):
    """历史工程风险条目列表响应"""
    model_config = ConfigDict(extra="ignore")

    total: int
    items: List[HistoricalAuditRiskResponse] = Field(default_factory=list)

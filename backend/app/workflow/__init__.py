"""
LangGraph 双智能体反思状态机与历史经验风险预警模块
backend/app/workflow/__init__.py
对齐 Phase 3 (M3) 特性 (Features 23-30)
"""

from app.workflow.contracts import (
    AuditFeedback,
    AuditFeedbackSchema,
    GraphState,
    GraphStateSchema,
    HistoricalAuditRiskCreate,
    HistoricalAuditRiskResponse,
    HumanReviewPayload,
    PatchDiffItem,
    PatchDiffItemSchema,
    ProjectCharter,
    RiskInterceptionReport,
    RiskWarningItem,
)
from app.workflow.critic import CriticAgent
from app.workflow.generator import GeneratorAgent, assemble_generator_system_prompt
from app.workflow.graph import (
    DualAgentWorkflowRunner,
    PurePythonStateCheckpointer,
    build_dual_agent_workflow,
    get_workflow_checkpointer,
)
from app.workflow.hitl import resume_workflow
from app.workflow.risk_warning import (
    SEED_HISTORICAL_RISKS,
    HistoricalRiskSearchEngine,
    ProjectRiskInterceptor,
    seed_historical_risks,
)
from app.workflow.router import WorkflowRouter

__all__ = [
    # Contracts & Schemas
    "GraphState",
    "AuditFeedback",
    "PatchDiffItem",
    "GraphStateSchema",
    "AuditFeedbackSchema",
    "PatchDiffItemSchema",
    "HumanReviewPayload",
    "ProjectCharter",
    "RiskWarningItem",
    "RiskInterceptionReport",
    "HistoricalAuditRiskCreate",
    "HistoricalAuditRiskResponse",
    # Agents
    "GeneratorAgent",
    "assemble_generator_system_prompt",
    "CriticAgent",
    # Routing & State Machine
    "WorkflowRouter",
    "build_dual_agent_workflow",
    "get_workflow_checkpointer",
    "PurePythonStateCheckpointer",
    "DualAgentWorkflowRunner",
    "resume_workflow",
    # Historical Risk KB & Interception
    "HistoricalRiskSearchEngine",
    "ProjectRiskInterceptor",
    "SEED_HISTORICAL_RISKS",
    "seed_historical_risks",
]

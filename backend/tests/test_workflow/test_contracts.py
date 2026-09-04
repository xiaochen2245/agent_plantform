"""
单元测试: 工作流强类型契约与 Pydantic 规范校验
backend/tests/test_workflow/test_contracts.py
"""

import pytest
from pydantic import ValidationError

from app.models.audit_rag import SeverityLevel, TaskStatus
from app.workflow.contracts import (
    AuditFeedback,
    AuditFeedbackSchema,
    GraphState,
    GraphStateSchema,
    HumanReviewPayload,
    PatchDiffItem,
    PatchDiffItemSchema,
    ProjectCharter,
    RiskInterceptionReport,
    RiskWarningItem,
)


def test_patch_diff_item_structure():
    """验证 PatchDiffItem TypedDict 与 PatchDiffItemSchema Pydantic 规范"""
    item: PatchDiffItem = {
        "issue_id": "iss_diff_001",
        "target_section": "第2章 施工总工期规划",
        "error_quote": "总工期承诺 120 天",
        "suggested_replacement": "总工期严格承诺 90 天",
        "reason": "严重工期负偏离",
        "severity": SeverityLevel.CRITICAL,
    }
    assert item["issue_id"] == "iss_diff_001"
    assert item["severity"] == SeverityLevel.CRITICAL

    # Pydantic 验证
    schema = PatchDiffItemSchema(**item)
    assert schema.issue_id == "iss_diff_001"
    assert schema.severity == SeverityLevel.CRITICAL

    # 序列化为字典
    dumped = schema.model_dump()
    assert dumped["target_section"] == "第2章 施工总工期规划"


def test_audit_feedback_schema_validation():
    """验证 AuditFeedbackSchema 得分约束与结构验证"""
    fb_dict = {
        "passed": False,
        "score": 65.5,
        "hallucination_detected": True,
        "issues": [
            {
                "issue_id": "iss_01",
                "target_section": "第4章 设备方案",
                "error_quote": "COP 为 4.8",
                "suggested_replacement": "COP 为 5.4",
                "reason": "设备能效不达标",
                "severity": SeverityLevel.HIGH,
            }
        ],
        "summary_comment": "未通过审查，存在高危设备偏差",
    }
    schema = AuditFeedbackSchema(**fb_dict)
    assert schema.passed is False
    assert schema.score == 65.5
    assert len(schema.issues) == 1

    # 验证无效评分 (> 100) 抛出异常
    with pytest.raises(ValidationError):
        AuditFeedbackSchema(
            passed=True, score=120.0, issues=[], summary_comment=""
        )


def test_graph_state_schema_defaults():
    """验证 GraphStateSchema 默认值与约束"""
    state_dict = {
        "tenant_id": "tenant_001",
        "task_id": "task_audit_001",
        "thread_id": "thread_abc_123",
        "draft": "初始方案草案内容",
    }
    schema = GraphStateSchema(**state_dict)
    assert schema.tenant_id == "tenant_001"
    assert schema.iteration_count == 0
    assert schema.max_iterations == 2
    assert schema.status == TaskStatus.PROCESSING
    assert schema.review_history == []


def test_project_charter_to_embedding_text():
    """验证 ProjectCharter 立项参数与向量文本转化方法"""
    charter = ProjectCharter(
        project_name="高新科技园三期综合楼",
        project_type="房建",
        scale_description="总建筑面积 8.5 万㎡，地下 2 层",
        duration_days=90,
        budget_cny_ten_thousand=1500.0,
        excavation_depth_meters=6.5,
        special_conditions=["雨季施工", "超危大深基坑"],
        charter_text="本工程为省级重点工程",
    )
    embed_text = charter.to_embedding_text()
    assert "工程类别：房建" in embed_text
    assert "工程名称：高新科技园三期综合楼" in embed_text
    assert "工期承诺：90日历天" in embed_text
    assert "基坑开挖深度：6.5米" in embed_text
    assert "特殊工况特征：雨季施工, 超危大深基坑" in embed_text


def test_human_review_payload_decisions():
    """验证 HumanReviewPayload 支持的决策类型"""
    payload_approve = HumanReviewPayload(thread_id="t_001", decision="approve")
    assert payload_approve.decision == "approve"

    payload_override = HumanReviewPayload(
        thread_id="t_002",
        human_patch="人工特批：工期调整为 90 天",
        decision="override_and_finish",
    )
    assert payload_override.human_patch is not None

    with pytest.raises(ValidationError):
        HumanReviewPayload(thread_id="t_003", decision="invalid_action")  # type: ignore

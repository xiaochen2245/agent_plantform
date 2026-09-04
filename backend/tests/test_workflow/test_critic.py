"""
单元测试: 校核智能体 (Critic Agent) 反幻觉核验、工期数值核对与 Patch Diff 生成
backend/tests/test_workflow/test_critic.py
"""

import pytest

from app.models.audit_rag import SeverityLevel, TaskStatus
from app.workflow.contracts import GraphState
from app.workflow.critic import CriticAgent


@pytest.mark.asyncio
async def test_critic_detects_schedule_negative_deviation():
    """验证工期负偏离检测: 招标文件要求 90 天，草案承诺 120 天，触发 CRITICAL 缺陷并生成 Patch Diff"""
    critic = CriticAgent()
    draft = (
        "第1章 项目总体概述\n"
        "工程概况良好。\n\n"
        "第2章 施工总工期规划与进度保障措施\n"
        "工程总工期承诺为 120 个日历天，确保竣工。\n\n"
        "第3章 质量安全管理\n"
        "安全第一。"
    )

    state: GraphState = {
        "tenant_id": "tenant_test",
        "task_id": "task_audit_01",
        "thread_id": "th_01",
        "draft": draft,
        "rfp_requirements": "招标文件第1章明确要求总工期不得超过 90 个日历天，此为实质性条款",
        "context_chunks": [],
        "iteration_count": 0,
        "max_iterations": 2,
        "status": TaskStatus.PROCESSING,
        "review_history": [],
    }

    result = await critic.critic_node(state)
    feedback = result["audit_feedback"]

    # 1. 验证审核未通过且标记幻觉/数据矛盾
    assert feedback["passed"] is False
    assert feedback["hallucination_detected"] is True
    assert feedback["score"] < 85.0

    # 2. 验证生成工期 Patch Diff
    issues = feedback["issues"]
    assert len(issues) >= 1
    sched_issue = next((i for i in issues if "工期" in i["reason"] or "工期" in i["target_section"]), None)
    assert sched_issue is not None
    assert sched_issue["severity"] == SeverityLevel.CRITICAL
    assert sched_issue["error_quote"] == "工程总工期承诺为 120 个日历天"
    assert "90 个日历天" in sched_issue["suggested_replacement"]

    # 3. 验证审计历史记录
    assert len(result["review_history"]) == 1
    assert result["review_history"][0]["action"] == "critic_audit_completed"
    assert result["review_history"][0]["passed"] is False


@pytest.mark.asyncio
async def test_critic_detects_equipment_cop_violation():
    """验证机电设备能效参数门槛核对: 要求 COP >= 5.0，草案为 4.8，检出 HIGH 缺陷"""
    critic = CriticAgent()
    draft = (
        "第4章 机电暖通专项设备配置方案\n"
        "额定能效比 COP 为 4.8，满足一般要求。"
    )
    state: GraphState = {
        "tenant_id": "tenant_test",
        "task_id": "task_audit_02",
        "thread_id": "th_02",
        "draft": draft,
        "rfp_requirements": "机电设备要求额定能效比 COP 不低于 5.0",
        "context_chunks": [],
        "iteration_count": 0,
        "review_history": [],
    }

    result = await critic.critic_node(state)
    feedback = result["audit_feedback"]

    equip_issue = next((i for i in feedback["issues"] if "COP" in i["error_quote"]), None)
    assert equip_issue is not None
    assert equip_issue["severity"] == SeverityLevel.HIGH
    assert "COP 为 5.4" in equip_issue["suggested_replacement"]


@pytest.mark.asyncio
async def test_critic_passes_compliant_proposal():
    """验证全面合规技术标方案顺利通过审查 (passed=True, score >= 85.0, issues 为空)"""
    critic = CriticAgent()
    compliant_draft = (
        "【某智能化工程技术标方案】\n\n"
        "第1章 项目总体概述\n"
        "本项目全面响应招标文件各项要求。\n\n"
        "第2章 施工总工期规划与进度保障措施\n"
        "工程总工期严格承诺为 90 个日历天，配置夜间流水与双班轮作业。\n\n"
        "第3章 质量安全与文明施工管理\n"
        "严格遵照国家质量验收统一标准 GB 50300 执行，安全生产零重大事故。\n\n"
        "第4章 机电暖通专项设备配置方案\n"
        "冷水机组选用一级能效设备，额定能效比 COP 为 5.4，风机能耗全面达标。"
    )

    state: GraphState = {
        "tenant_id": "tenant_test",
        "task_id": "task_audit_03",
        "thread_id": "th_03",
        "draft": compliant_draft,
        "rfp_requirements": "总工期不得超过 90 个日历天，COP 不低于 5.0",
        "context_chunks": [],
        "iteration_count": 1,
        "review_history": [],
    }

    result = await critic.critic_node(state)
    feedback = result["audit_feedback"]

    assert feedback["passed"] is True
    assert feedback["score"] >= 85.0
    assert feedback["hallucination_detected"] is False
    assert len(feedback["issues"]) == 0
    assert "准予通过" in feedback["summary_comment"]


def test_critic_to_review_results():
    """验证 AuditFeedback 转换为持久化 ReviewResult 实体"""
    critic = CriticAgent()
    feedback = {
        "passed": False,
        "score": 65.0,
        "hallucination_detected": True,
        "issues": [
            {
                "issue_id": "iss_test_01",
                "target_section": "第2章",
                "error_quote": "120天",
                "suggested_replacement": "90天",
                "reason": "工期超限",
                "severity": SeverityLevel.CRITICAL,
            }
        ],
        "summary_comment": "测试不通过",
    }
    results = critic.to_review_results("task_123", "tenant_abc", feedback)
    assert len(results) == 1
    rr = results[0]
    assert rr.task_id == "task_123"
    assert rr.tenant_id == "tenant_abc"
    assert rr.severity == SeverityLevel.CRITICAL
    assert rr.source_quote == "120天"

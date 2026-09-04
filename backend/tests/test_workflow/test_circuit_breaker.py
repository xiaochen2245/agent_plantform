"""
单元测试: 2 次迭代熔断器控制机制 (Feature 26)
验证最大重写轮次严格受限于 2 次以内，超限正确熔断流转至 human_review 并挂起
"""

import pytest

from app.models.audit_rag import SeverityLevel, TaskStatus
from app.workflow.contracts import AuditFeedback, GraphState
from app.workflow.critic import CriticAgent
from app.workflow.generator import GeneratorAgent
from app.workflow.graph import build_dual_agent_workflow
from app.workflow.router import WorkflowRouter


def test_router_approved_when_passed():
    """验证当校核通过 (passed=True) 时，路由流转至 approved"""
    state: GraphState = {
        "iteration_count": 1,
        "max_iterations": 2,
        "audit_feedback": {
            "passed": True,
            "score": 95.0,
            "hallucination_detected": False,
            "issues": [],
            "summary_comment": "通过",
        },
    }
    decision = WorkflowRouter.should_continue(state)
    assert decision == "approved"


def test_router_generator_when_unpassed_and_under_limit():
    """验证校核未通过且未达轮次上限时，路由流转至 generator 进行反思重写"""
    # 轮次 0 -> generator
    state_0: GraphState = {
        "iteration_count": 0,
        "max_iterations": 2,
        "audit_feedback": {
            "passed": False,
            "score": 60.0,
            "hallucination_detected": True,
            "issues": [{"issue_id": "1", "severity": SeverityLevel.CRITICAL}],
            "summary_comment": "未通过",
        },
    }
    assert WorkflowRouter.should_continue(state_0) == "generator"

    # 轮次 1 -> generator
    state_1: GraphState = {
        "iteration_count": 1,
        "max_iterations": 2,
        "audit_feedback": state_0["audit_feedback"],
    }
    assert WorkflowRouter.should_continue(state_1) == "generator"


def test_router_circuit_breaker_at_two_iterations():
    """
    验证 2 次迭代熔断控制:
    当 iteration_count >= 2 且仍未通过时，必须 100% 触发熔断流转至 human_review
    """
    state_2: GraphState = {
        "iteration_count": 2,
        "max_iterations": 2,
        "audit_feedback": {
            "passed": False,
            "score": 50.0,
            "hallucination_detected": True,
            "issues": [{"issue_id": "1", "severity": SeverityLevel.CRITICAL}],
            "summary_comment": "二次审核仍未通过",
        },
    }
    assert WorkflowRouter.should_continue(state_2) == "human_review"

    # 验证防御性上限截断: 即使外部传入非法极大值 (如 max_iterations=10)，硬性上限仍为 2
    state_tampered: GraphState = {
        "iteration_count": 2,
        "max_iterations": 10,
        "audit_feedback": state_2["audit_feedback"],
    }
    assert WorkflowRouter.should_continue(state_tampered) == "human_review"


class AlwaysFailingCritic(CriticAgent):
    """对抗性测试桩: 始终判定不通过，模拟不可调和的矛盾"""
    def _perform_audit(self, draft, rfp, contexts, iteration, guardrails=None):
        return {
            "passed": False,
            "score": 40.0,
            "hallucination_detected": True,
            "issues": [
                {
                    "issue_id": f"adversarial_{iteration}",
                    "target_section": "第2章",
                    "error_quote": "不可调和矛盾",
                    "suggested_replacement": "无法修正",
                    "reason": "对抗性强制驳回",
                    "severity": SeverityLevel.CRITICAL,
                }
            ],
            "summary_comment": f"第 {iteration} 轮强制驳回",
        }


@pytest.mark.asyncio
async def test_state_machine_halts_at_circuit_breaker():
    """
    端到端状态机熔断测试:
    面对持续不可调和的缺陷，验证状态机在严格执行 2 轮反思后触发熔断，
    状态流转至 HUMAN_REVIEW 并挂起，绝不发生死循环 (iteration_count == 2)
    """
    generator = GeneratorAgent()
    critic = AlwaysFailingCritic()
    app = build_dual_agent_workflow(generator_agent=generator, critic_agent=critic)

    initial_state: GraphState = {
        "tenant_id": "tenant_cb_test",
        "task_id": "task_cb_001",
        "thread_id": "th_cb_001",
        "rfp_requirements": "对抗性招标文件",
        "context_chunks": [],
        "iteration_count": 0,
        "max_iterations": 2,
        "status": TaskStatus.PROCESSING,
        "review_history": [],
    }

    final_state = await app.ainvoke(initial_state)

    # 验证最终状态为 HUMAN_REVIEW
    assert final_state["status"] == TaskStatus.HUMAN_REVIEW

    # 验证反思轮次严格等于 2 (初稿第0轮 + 第1轮重写 + 第2轮重写后熔断)
    assert final_state["iteration_count"] == 2

    # 验证审计追踪历史记录了所有轮次
    actions = [h["action"] for h in final_state.get("review_history", [])]
    assert "initial_draft_generation" in actions
    assert "patch_diff_targeted_revision" in actions

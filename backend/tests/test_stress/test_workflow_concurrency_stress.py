"""
双智能体状态机高并发反思与熔断恢复压力测试套件 (test_workflow_concurrency_stress.py)
验证:
1. 20 个并发 LangGraph 反思工作流实例多租户并发流转与终态 SUCCESS
2. 10 个并发不可调和缺陷任务触发 2 轮反思熔断至 HUMAN_REVIEW
3. Checkpointer 状态隔离与 resume_workflow 人工补丁注入恢复
"""

import asyncio
import pytest

from app.models.audit_rag import SeverityLevel, TaskStatus
from app.workflow.contracts import GraphState
from app.workflow.critic import CriticAgent
from app.workflow.generator import GeneratorAgent
from app.workflow.graph import build_dual_agent_workflow, get_workflow_checkpointer
from app.workflow.hitl import resume_workflow


class AdversarialAlwaysFailingCritic(CriticAgent):
    """对抗性质检智能体: 模拟不可调和的工程死锁缺陷，强制驳回"""
    def _perform_audit(self, draft, rfp, contexts, iteration, guardrails=None):
        return {
            "passed": False,
            "score": 45.0,
            "hallucination_detected": True,
            "issues": [
                {
                    "issue_id": f"deadlock_{iteration}",
                    "target_section": "工期规划与技术方案",
                    "error_quote": "30个日历天内竣工验收",
                    "suggested_replacement": "物理极限死锁，无法在合法工程质量标准下满足",
                    "reason": "招标工期极端压缩且不允许调整，违反国家强标工期底线",
                    "severity": SeverityLevel.CRITICAL,
                }
            ],
            "summary_comment": f"第 {iteration} 轮审核: 存在不可调和工程死锁，强制驳回",
        }


class TestWorkflowConcurrencyStress:
    """双智能体工作流高并发与熔断恢复压力测试"""

    @pytest.mark.asyncio
    async def test_20_concurrent_dual_agent_workflows(self):
        """验证 20 个并发双智能体工作流独立执行反思闭环并全量流转至 SUCCESS"""
        workflow_app = build_dual_agent_workflow()
        concurrency = 20

        initial_states = []
        for i in range(concurrency):
            state: GraphState = {
                "tenant_id": f"tenant_wf_stress_{i}",
                "task_id": f"task_wf_stress_{i}",
                "thread_id": f"th_wf_stress_{i}",
                "rfp_requirements": f"智慧科技园区第 {i} 标段机电安装工程，总工期严格限制在 90 个日历天内，设备 COP 不低于 5.0",
                "context_chunks": [
                    {
                        "chunk_id": f"chk_spec_{i}",
                        "content": f"第 {i} 标段招标文件要求：工期上限 90 个日历天；冷机 COP >= 5.0。",
                    }
                ],
                "iteration_count": 0,
                "max_iterations": 2,
                "status": TaskStatus.PROCESSING,
                "review_history": [],
            }
            initial_states.append(state)

        async def run_single_workflow(state: GraphState):
            return await workflow_app.ainvoke(state)

        tasks = [run_single_workflow(s) for s in initial_states]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 验证全部 20 个工作流成功流转至 SUCCESS 且各租户上下文独立
        assert len(results) == concurrency
        for idx, res in enumerate(results):
            assert not isinstance(res, Exception), f"工作流 {idx} 异常失败: {res}"
            assert res["status"] == TaskStatus.SUCCESS, f"工作流 {idx} 终态异常: {res['status']}"
            assert res["tenant_id"] == f"tenant_wf_stress_{idx}"
            assert res["thread_id"] == f"th_wf_stress_{idx}"
            assert 1 <= res["iteration_count"] <= 2, f"反思轮次异常: {res['iteration_count']}"

            # 校验反思修正内容注入
            draft = res.get("draft", "")
            assert "90 个日历天" in draft
            assert "COP 为 5.4" in draft

            # 校验审计记录独立存在
            actions = [h.get("action") for h in res.get("review_history", [])]
            assert "initial_draft_generation" in actions
            assert "critic_audit_completed" in actions
            assert "patch_diff_targeted_revision" in actions

    @pytest.mark.asyncio
    async def test_concurrent_circuit_breakers_and_checkpointer_recovery(self):
        """
        验证 10 个并发工作流因不可调和缺陷触发 2 次迭代硬熔断挂起至 HUMAN_REVIEW，
        并验证 Checkpointer 快照隔离与 resume_workflow 人工特批恢复
        """
        concurrency = 10
        failing_critic = AdversarialAlwaysFailingCritic()
        breaker_app = build_dual_agent_workflow(critic_agent=failing_critic)
        checkpointer = get_workflow_checkpointer()

        breaker_states = []
        for i in range(concurrency):
            t_id = f"tenant_cb_{i}"
            th_id = f"th_cb_stress_{i}"
            state: GraphState = {
                "tenant_id": t_id,
                "task_id": f"task_cb_{i}",
                "thread_id": th_id,
                "rfp_requirements": "极端工期压缩招标文件：要求主体智能化在30天内竣工验收，不允许任何合理工期调整",
                "context_chunks": [],
                "iteration_count": 0,
                "max_iterations": 2,
                "status": TaskStatus.PROCESSING,
                "review_history": [],
            }
            breaker_states.append(state)

        async def run_failing_workflow(state: GraphState):
            return await breaker_app.ainvoke(state)

        tasks = [run_failing_workflow(s) for s in breaker_states]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 1. 验证全部 10 个并发任务均准确触发 2 次反思熔断并安全流转至 HUMAN_REVIEW
        for idx, res in enumerate(results):
            assert not isinstance(res, Exception), f"熔断任务 {idx} 异常崩溃: {res}"
            assert res["status"] == TaskStatus.HUMAN_REVIEW, f"任务 {idx} 未能熔断至 HUMAN_REVIEW: {res['status']}"
            assert res["iteration_count"] == 2, f"任务 {idx} 迭代轮次不符合熔断门禁: {res['iteration_count']}"

        # 2. 验证 Checkpointer 针对每个 thread_id 均持久化了独立状态快照
        for i in range(concurrency):
            th_id = f"th_cb_stress_{i}"
            snapshot = checkpointer.get(th_id)
            assert snapshot is not None, f"Checkpointer 丢失 thread_id={th_id} 的状态快照"
            assert snapshot["thread_id"] == th_id
            assert snapshot["status"] == TaskStatus.HUMAN_REVIEW

        # 3. 选取部分任务执行 resume_workflow 人工特批恢复 (HITL)
        recovery_threads = [f"th_cb_stress_{i}" for i in [0, 4, 8]]
        for th_id in recovery_threads:
            human_patch = "【专家特批条款】经建设方专家论证会特批决议，工期根据国家定额标准依法调整为90个日历天，原30天要求作废。"
            resumed_state = await resume_workflow(
                thread_id=th_id,
                human_patch=human_patch,
                decision="override_and_finish",
                checkpointer=checkpointer,
            )
            # 验证人工特批后流转至 SUCCESS 终态
            assert resumed_state["status"] == TaskStatus.SUCCESS
            assert human_patch in resumed_state["draft"]
            # 验证审计历史追加了 human_intervention 事件
            history = resumed_state["review_history"]
            human_actions = [h for h in history if h.get("action") == "human_intervention"]
            assert len(human_actions) == 1
            assert human_actions[0]["decision"] == "override_and_finish"

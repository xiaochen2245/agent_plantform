"""
端到端集成测试: LangGraph 双智能体闭环反思状态机全生命周期
backend/tests/test_workflow/test_workflow_e2e.py
验证:
1. Generator 初稿生成 (含初始工期偏差)
2. Critic 质检反幻觉核验并生成 Patch Diff
3. Generator 靶向手术重写 (冻结合规文本)
4. Critic 二次复核通过并流转至 SUCCESS 终态
5. 历史风险预防护栏全流程注入与闭环
"""

import pytest

from app.models.audit_rag import SeverityLevel, TaskStatus
from app.workflow.contracts import GraphState, ProjectCharter
from app.workflow.critic import CriticAgent
from app.workflow.generator import GeneratorAgent
from app.workflow.graph import build_dual_agent_workflow
from app.workflow.risk_warning import ProjectRiskInterceptor, seed_historical_risks
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.db.base import Base
from app.models.audit_rag import Tenant


@pytest.mark.asyncio
async def test_dual_agent_reflection_loop_e2e():
    """
    完整端到端双智能体反思闭环流转测试:
    1. Generator 拟定初稿 (产生 120 天工期偏差)
    2. Critic 检出严重工期负偏离，扣分至 65 分，输出结构化 Patch Diff
    3. Router 触发反思回流 (第 1 轮)
    4. Generator 靶向手术修订，替换工期为 90 天，其他章节原样冻结
    5. Critic 二次复核通过 (score >= 85.0, passed=True)
    6. Router 决策 approved，状态机平稳退出至 SUCCESS
    """
    workflow_app = build_dual_agent_workflow()

    initial_state: GraphState = {
        "tenant_id": "tenant_e2e_01",
        "task_id": "task_e2e_01",
        "thread_id": "th_e2e_01",
        "rfp_requirements": "智能化工程项目，招标文件明确总工期要求严格限制在 90 个日历天内，设备 COP 不低于 5.0",
        "context_chunks": [
            {
                "chunk_id": "chk_01",
                "content": "招标技术标规范：工程总工期不得超过 90 个日历天；冷水机组额定能效比 COP 不低于 5.0。",
            }
        ],
        "iteration_count": 0,
        "max_iterations": 2,
        "status": TaskStatus.PROCESSING,
        "review_history": [],
    }

    # 执行状态机全闭环运行
    final_state = await workflow_app.ainvoke(initial_state)

    # 1. 验证终态为成功 (SUCCESS)
    assert final_state["status"] == TaskStatus.SUCCESS

    # 2. 验证完成了反思重写并成功修正 (轮次受限于 2 次以内)
    assert final_state["iteration_count"] <= 2
    assert final_state["iteration_count"] >= 1

    # 3. 验证终版草案包含已纠偏的合规文本
    final_draft = final_state["draft"]
    assert "90 个日历天" in final_draft
    assert "120 个日历天" not in final_draft
    # 设备 COP 在第 1 轮由 4.8 纠偏为 5.4
    assert "COP 为 5.4" in final_draft

    # 4. 验证最新审核反馈为通过且评分达标
    feedback = final_state["audit_feedback"]
    assert feedback is not None
    assert feedback["passed"] is True
    assert feedback["score"] >= 85.0
    assert len(feedback["issues"]) == 0

    # 5. 验证审计历史完整记录了全链路事件 (初稿拟定 -> 首次核验驳回 -> 靶向修订 -> 二次核验通过)
    actions = [h.get("action") for h in final_state.get("review_history", [])]
    assert "initial_draft_generation" in actions
    assert "critic_audit_completed" in actions
    assert "patch_diff_targeted_revision" in actions


@pytest.mark.asyncio
async def test_proactive_interceptor_to_generator_closed_loop():
    """
    立项主动拦截 -> 预防护栏提示词注入 -> 方案起草与核验全闭环:
    验证立项阶段拦截到的深基坑事故经验成功注入生成器并在初稿中体现
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        t_id = "tenant_e2e_intercept"
        session.add(Tenant(id=t_id, code="T_INT", name="集成测试租户"))
        await session.commit()
        await seed_historical_risks(session, tenant_id=t_id)

        # 1. 前置主动拦截诊断
        interceptor = ProjectRiskInterceptor()
        charter = ProjectCharter(
            project_name="滨江科技中心深基坑项目",
            project_type="房建",
            scale_description="总建面8万㎡，地下3层，开挖深度6.5m",
            excavation_depth_meters=6.5,
            special_conditions=["深基坑", "富水地层", "超危大工程"],
        )
        report = await interceptor.intercept_project_risks(
            session=session, tenant_id=t_id, charter=charter
        )

        assert report.total_risks_matched >= 1
        assert report.guardrail_system_prompt_snippet != ""

        # 2. 将拦截报告的防护栏注入双智能体状态机
        workflow_app = build_dual_agent_workflow()
        initial_state: GraphState = {
            "tenant_id": t_id,
            "task_id": "task_intercept_01",
            "thread_id": "th_intercept_01",
            "rfp_requirements": "滨江科技中心项目施工方案，总工期不得超过 90 个日历天",
            "risk_guardrails": report.guardrail_system_prompt_snippet,
            "context_chunks": [],
            "iteration_count": 0,
            "max_iterations": 2,
            "status": TaskStatus.PROCESSING,
            "review_history": [],
        }

        final_state = await workflow_app.ainvoke(initial_state)

        # 3. 验证终版方案成功融入防线且通过全部审查
        assert final_state["status"] == TaskStatus.SUCCESS
        assert "90 个日历天" in final_state["draft"]
        assert ("超危大工程" in final_state["draft"]) or ("专家论证" in final_state["draft"])

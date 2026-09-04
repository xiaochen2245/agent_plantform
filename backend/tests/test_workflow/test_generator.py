"""
单元测试: 生成智能体 (Generator Agent) 初稿生成与 Patch Diff 靶向手术修订
backend/tests/test_workflow/test_generator.py
"""

import pytest

from app.models.audit_rag import SeverityLevel, TaskStatus
from app.workflow.contracts import AuditFeedback, GraphState, PatchDiffItem
from app.workflow.generator import GeneratorAgent, assemble_generator_system_prompt


@pytest.mark.asyncio
async def test_initial_draft_generation():
    """验证初始阶段 (iteration=0) 方案初稿正常生成并记录审计追踪"""
    agent = GeneratorAgent()
    state: GraphState = {
        "tenant_id": "tenant_test",
        "task_id": "task_001",
        "thread_id": "th_001",
        "rfp_requirements": "智能化工程，总工期要求严格限制在 90 天内",
        "context_chunks": [{"content": "国家绿色建筑评价标准要求冷水机组 COP 不低于 5.0"}],
        "iteration_count": 0,
        "max_iterations": 2,
        "status": TaskStatus.PROCESSING,
        "review_history": [],
    }

    result = await agent.generate_node(state)

    assert "draft" in result
    draft = result["draft"]
    assert "第1章 项目总体概述" in draft
    assert "第2章 施工总工期规划与进度保障措施" in draft
    assert "第3章 质量安全与文明施工管理" in draft
    assert "第4章 机电暖通专项设备配置方案" in draft

    assert result["status"] == TaskStatus.PROCESSING
    assert len(result["review_history"]) == 1
    assert result["review_history"][0]["action"] == "initial_draft_generation"
    assert result["review_history"][0]["patches_applied"] == 0


@pytest.mark.asyncio
async def test_generator_guardrail_prompt_injection():
    """验证历史风险预警预防护栏注入并在初稿中体现防护条款"""
    agent = GeneratorAgent()
    guardrails = [
        "针对深基坑超危大工程，严格落实专项支护设计与不少于5位省级专家论证程序",
        "针对雨季施工，编制关键路径双班倒网络图并配置移动排水泵站",
    ]
    state: GraphState = {
        "tenant_id": "tenant_test",
        "task_id": "task_002",
        "thread_id": "th_002",
        "rfp_requirements": "深基坑房建项目",
        "context_chunks": [],
        "risk_guardrails": guardrails,
        "iteration_count": 0,
        "max_iterations": 2,
        "status": TaskStatus.PROCESSING,
        "review_history": [],
    }

    result = await agent.generate_node(state)
    draft = result["draft"]

    # 验证草稿成功融入预防护栏条款
    assert "超危大工程" in draft or "专家论证" in draft
    assert "雨季" in draft or "排水" in draft

    # 验证 assemble_generator_system_prompt 格式化输出
    system_prompt = assemble_generator_system_prompt("基础专家提示词", guardrails)
    assert "历史工程事故与审计风险强制预防护栏" in system_prompt
    assert "不少于5位省级专家论证程序" in system_prompt


@pytest.mark.asyncio
async def test_patch_diff_surgical_revision():
    """
    验证基于 Patch Diff 靶向手术式修订 (iteration > 0):
    精准替换工期负偏离条款，其他 95%+ 合规章节原样保留 (文本冻结)
    """
    agent = GeneratorAgent()
    initial_draft = (
        "第1章 项目总体概述\n"
        "本项目建设严格对齐高可靠标准。\n\n"
        "第2章 施工总工期规划与进度保障措施\n"
        "经过项目部详细测算，本项目工程总工期承诺为 120 个日历天，确保竣工。\n\n"
        "第3章 质量安全管理\n"
        "严格遵照 GB 50300 施工。"
    )

    patch_issue: PatchDiffItem = {
        "issue_id": "iss_sched_0_01",
        "target_section": "第2章 施工总工期规划与进度保障措施",
        "error_quote": "工程总工期承诺为 120 个日历天",
        "suggested_replacement": "工程总工期严格承诺为 90 个日历天，配置夜间流水与双班轮作业",
        "reason": "工期负偏离严重违规",
        "severity": SeverityLevel.CRITICAL,
    }

    feedback: AuditFeedback = {
        "passed": False,
        "score": 65.0,
        "hallucination_detected": True,
        "issues": [patch_issue],
        "summary_comment": "检出工期负偏离",
    }

    state: GraphState = {
        "tenant_id": "tenant_test",
        "task_id": "task_003",
        "thread_id": "th_003",
        "draft": initial_draft,
        "audit_feedback": feedback,
        "iteration_count": 1,
        "max_iterations": 2,
        "status": TaskStatus.PROCESSING,
        "review_history": [],
    }

    result = await agent.generate_node(state)
    revised_draft = result["draft"]

    # 1. 验证目标文本被精准替换为 90 天
    assert "工程总工期严格承诺为 90 个日历天，配置夜间流水与双班轮作业" in revised_draft
    assert "120 个日历天" not in revised_draft

    # 2. 验证非缺陷章节文本 100% 保持原样冻结
    assert "第1章 项目总体概述\n本项目建设严格对齐高可靠标准。" in revised_draft
    assert "第3章 质量安全管理\n严格遵照 GB 50300 施工。" in revised_draft

    # 3. 验证审计追踪
    assert len(result["review_history"]) == 1
    entry = result["review_history"][0]
    assert entry["action"] == "patch_diff_targeted_revision"
    assert entry["patches_applied"] == 1


@pytest.mark.asyncio
async def test_patch_diff_whitespace_normalization():
    """验证包含多余空格与空白换行的容错正则替换"""
    agent = GeneratorAgent()
    draft = "总工期   承诺为   120  个日历天  ，已完成测算。"
    feedback: AuditFeedback = {
        "passed": False,
        "score": 65.0,
        "hallucination_detected": True,
        "issues": [
            {
                "issue_id": "iss_ws_01",
                "target_section": "",
                "error_quote": "总工期 承诺为 120 个日历天",
                "suggested_replacement": "总工期严格承诺为 90 个日历天",
                "reason": "容错测试",
                "severity": SeverityLevel.CRITICAL,
            }
        ],
        "summary_comment": "",
    }
    state: GraphState = {
        "draft": draft,
        "audit_feedback": feedback,
        "iteration_count": 1,
        "review_history": [],
    }

    result = await agent.generate_node(state)
    assert "总工期严格承诺为 90 个日历天" in result["draft"]

"""
生成智能体 (Generator Agent - Feature 23)
负责工程方案初稿拟定与根据 Critic 的 Patch Diff 进行靶向手术式修订
"""

import datetime
import logging
import re
from typing import Any, Dict, List, Optional, Union

from app.models.audit_rag import TaskStatus
from app.workflow.contracts import AuditFeedback, GraphState, PatchDiffItem

logger = logging.getLogger(__name__)


def assemble_generator_system_prompt(
    base_prompt: str, risk_guardrails: Optional[Union[List[str], str]] = None
) -> str:
    """组合基础系统提示词与历史风险预防护栏 (Feature 30)"""
    if not risk_guardrails:
        return base_prompt

    if isinstance(risk_guardrails, list):
        guardrail_text = "\n".join(f"- {g}" for g in risk_guardrails)
    else:
        guardrail_text = str(risk_guardrails)

    return (
        f"{base_prompt}\n\n"
        f"【历史工程事故与审计风险强制预防护栏】:\n"
        f"{guardrail_text}\n"
        f"方案起草必须在相应章节针对上述红线给出明确的技术支撑措施与合规保障，杜绝空洞口号。"
    )


class GeneratorAgent:
    """
    生成智能体业务引擎
    支持：
    1. 初稿生成 (结合 RAG Parent Chunks 与前置历史风险预警防线)
    2. Patch Diff 靶向局部修改 (精准子串替换，冻结 95%+ 无缺陷文本，阻断二次幻觉)
    3. 结构化系统提示词装配与真实大模型 API / 测试桩兼容
    """

    def __init__(self, llm_client: Optional[Any] = None):
        self.llm_client = llm_client

    async def generate_node(self, state: GraphState) -> Dict[str, Any]:
        """LangGraph 节点入口函数"""
        iteration = state.get("iteration_count", 0)
        task_id = state.get("task_id", "")
        logger.info(f"[GeneratorAgent] 执行生成节点，task_id={task_id}, 当前轮次={iteration}")

        if iteration == 0:
            draft = await self._generate_initial_draft(state)
            action_desc = "initial_draft_generation"
            patches_count = 0
        else:
            draft = await self._revise_draft_with_patches(state)
            action_desc = "patch_diff_targeted_revision"
            feedback = state.get("audit_feedback") or {}
            patches_count = len(feedback.get("issues", []))

        # 记录审计追踪历史
        history_entry = {
            "iteration": iteration,
            "action": action_desc,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "patches_applied": patches_count,
            "draft_preview": draft[:200] + "..." if len(draft) > 200 else draft,
        }

        new_history = list(state.get("review_history", []))
        new_history.append(history_entry)

        return {
            "draft": draft,
            "status": TaskStatus.PROCESSING,
            "review_history": new_history,
        }

    async def _generate_initial_draft(self, state: GraphState) -> str:
        """根据 RFP 需求、RAG 父切片和历史风险预警生成方案初稿"""
        rfp = state.get("rfp_requirements", "")
        contexts = state.get("context_chunks", [])
        risk_guardrails = state.get("risk_guardrails")

        # 若接入了真实外部大模型客户端 (如支持 acomplete / agenerate)
        if self.llm_client and hasattr(self.llm_client, "acomplete"):
            prompt = self._build_draft_prompt(rfp, contexts, risk_guardrails)
            return await self.llm_client.acomplete(prompt)

        # 离线环境与测试用高保真工程技术标初稿
        # 针对包含深基坑与雨季等特殊工况标签进行护栏响应
        guardrail_clause = ""
        guardrail_str = str(risk_guardrails or "")
        if "超危大" in guardrail_str or "基坑" in guardrail_str or "5m" in guardrail_str:
            guardrail_clause += "\n- 针对深基坑超危大工程，严格落实专项支护设计与不少于5位省级专家论证程序，配置24小时自动化地下水位与沉降位移监测。"
        if "雨季" in guardrail_str or "排水" in guardrail_str:
            guardrail_clause += "\n- 针对雨季施工，编制关键路径双班倒网络图，配置2套移动排水泵站与应急遮雨大棚，确保工期不中断。"

        return (
            "【某智能化工程技术标方案】\n\n"
            "第1章 项目总体概述\n"
            "本项目建设严格对齐高可靠、高能效技术标准，全面响应建设单位招标文件各项实质性要求。\n\n"
            "第2章 施工总工期规划与进度保障措施\n"
            "经过项目部详细编制的流水施工网络进度计划测算，本项目工程总工期承诺为 120 个日历天，确保工程保质保量竣工验收。\n\n"
            "第3章 质量安全与文明施工管理\n"
            "严格遵照国家质量验收统一标准 GB 50300 执行，安全生产零重大事故。"
            f"{guardrail_clause}\n\n"
            "第4章 机电暖通专项设备配置方案\n"
            "冷水机组额定能效比 COP 为 4.8，风机能耗全面达标，满足绿色建筑三星级认证要求。"
        )

    async def _revise_draft_with_patches(self, state: GraphState) -> str:
        """
        基于 Patch Diff 进行靶向手术式修订
        策略 1: 原文精确替换
        策略 2: 正则空格容错替换
        策略 3: 章节范围锚定替换
        保证非缺陷章节文本 100% 冻结不变
        """
        current_draft = state.get("draft", "")
        feedback: Optional[AuditFeedback] = state.get("audit_feedback")

        if not feedback or not feedback.get("issues"):
            return current_draft

        revised_draft = current_draft
        for issue in feedback["issues"]:
            error_quote = (issue.get("error_quote") or issue.get("original_text") or "").strip()
            replacement = (issue.get("suggested_replacement") or issue.get("suggested_patch") or "").strip()

            if not error_quote:
                continue

            # 策略 1: 原文精确单次替换
            if error_quote in revised_draft:
                revised_draft = revised_draft.replace(error_quote, replacement, 1)
                continue

            # 策略 2: 消除多余空白与换行符的正则容错替换
            pattern_str = re.escape(re.sub(r"\s+", " ", error_quote))
            pattern_str = pattern_str.replace(r"\ ", r"\s+")
            if re.search(pattern_str, revised_draft):
                revised_draft = re.sub(pattern_str, replacement, revised_draft, count=1)
                continue

            # 策略 3: 基于 target_section / location 锚点范围进行局部替换
            target_section = (issue.get("target_section") or issue.get("location") or "").strip()
            if target_section and target_section in revised_draft:
                section_idx = revised_draft.find(target_section)
                post_text = revised_draft[section_idx:]
                if error_quote in post_text:
                    post_text = post_text.replace(error_quote, replacement, 1)
                    revised_draft = revised_draft[:section_idx] + post_text
                    continue

        return revised_draft

    def _build_draft_prompt(
        self,
        rfp: str,
        contexts: List[Dict[str, Any]],
        risk_guardrails: Optional[Union[List[str], str]],
    ) -> str:
        """构建结构化工程方案编写提示词"""
        context_text = "\n\n".join(c.get("content", "") for c in contexts)
        base_prompt = (
            f"你是国家注册一级建造师与高级招投标方案专家。\n"
            f"【招标文件要求】:\n{rfp}\n\n"
            f"【事实知识依据(RAG Context)】:\n{context_text}\n\n"
            f"请生成结构完整的工程技术标方案，严禁捏造与上下文矛盾的工期、造价与参数。"
        )
        return assemble_generator_system_prompt(base_prompt, risk_guardrails)

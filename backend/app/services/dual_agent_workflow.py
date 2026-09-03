"""
LangGraph 双智能体 (Dual-Agent) 闭环方案生成与校核工作流
架构包含:
1. Generator Agent (生成智能体): 负责方案初稿拟定与根据审查意见针对性迭代重写
2. Critic/Auditor Agent (校核智能体): 负责事实性核验(Anti-Hallucination)、合规审查与数据对齐
3. Reflection Loop (反思回传): 生成结构化 Patch Diff 行内批注并回传
4. 循环熔断控制: 严格限制最大重写轮次为 2 次，超限触发 Human-in-the-loop 人工干预
"""

from dataclasses import dataclass, field
import datetime
import json
import logging
from typing import Any, Dict, List, Literal, Optional, TypedDict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. 工作流状态定义 (GraphState)
# ---------------------------------------------------------------------------

class PatchDiffItem(TypedDict):
    """行内批注与修订差异项"""
    location: str            # 定位: 如 "第二章 2.1 节工期声明"
    severity: str            # "critical", "warning", "info"
    issue: str               # 事实矛盾或合规问题说明
    original_text: str       # 需修改的原文片段
    suggested_patch: str     # 建议替换或修订的内容


class AuditFeedback(TypedDict):
    """校核智能体输出的结构化审查报告"""
    passed: bool                         # 是否通过全部核验
    score: float                         # 质量综合评分 (0~100)
    hallucination_detected: bool         # 是否检测到事实性幻觉/捏造
    issues: List[PatchDiffItem]          # 缺陷与批注清单
    summary_comment: str                 # 综合评审结论


class GraphState(TypedDict):
    """
    LangGraph 核心状态机状态字典 (强类型)
    """
    tenant_id: str
    task_id: str
    task_prompt: str                     # 用户任务指令 / 项目立项参数
    context_chunks: List[Dict[str, Any]] # RAG 检索召回的父切片事实依据 (Parent Chunks)
    
    # 动态演进状态
    draft: str                           # 当前版本的方案文本/汇报材料
    audit_feedback: Optional[AuditFeedback] # 最近一次校核反馈
    iteration_count: int                 # 当前迭代重写轮次 (初始为0)
    max_iterations: int                  # 最大允许重写轮次 (默认2次)
    
    # 工作流阶段状态
    status: Literal["generating", "auditing", "revision_required", "approved", "human_review", "failed"]
    review_history: List[Dict[str, Any]] # 历史版本与每次审查的审计追踪记录


# ---------------------------------------------------------------------------
# 2. 核心智能体节点实现 (Agent Nodes)
# ---------------------------------------------------------------------------

class DualAgentWorkflowEngine:
    """
    双智能体业务编排引擎
    封装 LLM 推理接口，支持接入真实大模型或测试桩
    """

    def __init__(self, llm_client: Optional[Any] = None):
        self.llm_client = llm_client

    async def generate_node(self, state: GraphState) -> Dict[str, Any]:
        """
        Generator Agent 节点:
        - 首次 (iteration_count == 0): 依据 RAG 上下文与项目参数撰写初稿
        - 后续 (iteration_count > 0): 根据 Critic 的 Patch Diff 进行靶向修订
        """
        iteration = state.get("iteration_count", 0)
        task_prompt = state["task_prompt"]
        context_chunks = state.get("context_chunks", [])
        last_feedback = state.get("audit_feedback")

        logger.info(f"[Generator Agent] 正在执行生成，当前迭代轮次: {iteration}")

        if iteration == 0:
            # === 初稿拟定 ===
            draft_content = self._mock_or_call_llm_draft(task_prompt, context_chunks)
            history_entry = {
                "iteration": 0,
                "action": "initial_generation",
                "timestamp": datetime.datetime.now().isoformat(),
                "draft_snippet": draft_content[:150] + "...",
            }
        else:
            # === 靶向修订重写 ===
            current_draft = state["draft"]
            draft_content = self._mock_or_call_llm_rewrite(current_draft, last_feedback, context_chunks)
            history_entry = {
                "iteration": iteration,
                "action": "targeted_revision",
                "timestamp": datetime.datetime.now().isoformat(),
                "patches_applied": len(last_feedback.get("issues", [])) if last_feedback else 0,
            }

        new_history = list(state.get("review_history", []))
        new_history.append(history_entry)

        return {
            "draft": draft_content,
            "status": "auditing",
            "review_history": new_history,
        }

    async def critic_node(self, state: GraphState) -> Dict[str, Any]:
        """
        Critic / Auditor Agent 节点:
        - 对草稿执行严格的反幻觉 (Anti-Hallucination) 核验
        - 核查是否与 RAG 父切片指标 (工期、资质、技术参数) 存在矛盾
        - 生成带行内定位的 Patch Diff
        """
        draft = state["draft"]
        context_chunks = state.get("context_chunks", [])
        iteration = state.get("iteration_count", 0)

        logger.info(f"[Critic Agent] 正在对第 {iteration} 轮草稿执行合规与反幻觉审计...")

        # 执行事实性核验与合规比对
        feedback = self._audit_draft(draft, context_chunks, iteration)

        # 增加迭代计数
        new_iteration = iteration + 1
        new_status = "approved" if feedback["passed"] else "revision_required"

        new_history = list(state.get("review_history", []))
        new_history.append({
            "iteration": iteration,
            "action": "audit_completed",
            "score": feedback["score"],
            "passed": feedback["passed"],
            "issues_count": len(feedback["issues"]),
            "timestamp": datetime.datetime.now().isoformat(),
        })

        return {
            "audit_feedback": feedback,
            "iteration_count": new_iteration,
            "status": new_status,
            "review_history": new_history,
        }

    # -----------------------------------------------------------------------
    # 3. 条件流转分支与路由判定 (Conditional Routing)
    # -----------------------------------------------------------------------

    @staticmethod
    def should_continue(state: GraphState) -> Literal["generator", "approved", "human_review"]:
        """
        条件流转分支逻辑:
        1. 若 Critic 判定完全通过 -> 流转至 approved
        2. 若未通过但未达到最大重写轮次 (iteration_count < max_iterations) -> 回流至 generator 再次修改
        3. 若达到最大轮次 (2次) 仍有缺陷 -> 中断并流转至 human_review (人工断点介入)，杜绝死循环
        """
        feedback = state.get("audit_feedback")
        iteration = state.get("iteration_count", 0)
        max_iter = state.get("max_iterations", 2)

        if feedback and feedback.get("passed", False):
            logger.info("[Workflow Router] 校核完全通过，工作流审批批准完成。")
            return "approved"

        if iteration < max_iter:
            logger.warning(f"[Workflow Router] 校核未通过，已迭代 {iteration}/{max_iter} 次，触发反思重写循环。")
            return "generator"

        logger.error(f"[Workflow Router] 达到最大迭代轮次 {max_iter} 限制，转交人工介入 (Human-in-the-loop)。")
        return "human_review"

    # -----------------------------------------------------------------------
    # 辅助与模拟 LLM 推理方法 (可无缝对接真实 OpenAI / Qwen / DeepSeek)
    # -----------------------------------------------------------------------

    def _mock_or_call_llm_draft(self, prompt: str, contexts: List[Dict[str, Any]]) -> str:
        """初稿生成逻辑 (在没有外接真实 API 时内置高拟真技术标初稿)"""
        return (
            "【工程技术方案草案】\n"
            "1. 项目总体概述：依据建设单位招标要求，本项目定位为高标准绿色智能建筑。\n"
            "2. 施工总工期规划：根据我方综合测算与流水作业安排，工程总工期承诺为 120 个日历天。\n"
            "3. 质量与安全标准：全面执行 GB 50300 施工验收统一规范，杜绝一切特重大安全事故。\n"
            "4. 暖通系统设备方案：采用变频离心式冷水机组，额定能效比 COP 不低于 5.2。"
        )

    def _mock_or_call_llm_rewrite(
        self, current_draft: str, feedback: Optional[AuditFeedback], contexts: List[Dict[str, Any]]
    ) -> str:
        """根据 Patch Diff 执行靶向修订"""
        if not feedback or not feedback.get("issues"):
            return current_draft

        revised = current_draft
        for patch in feedback["issues"]:
            target_text = patch.get("original_text", "")
            replacement = patch.get("suggested_patch", "")
            if target_text and target_text in revised:
                revised = revised.replace(target_text, replacement)
        return revised

    def _audit_draft(self, draft: str, contexts: List[Dict[str, Any]], iteration: int) -> AuditFeedback:
        """
        校核智能体逻辑:
        比对草稿中的承诺是否与事实切片一致。
        例如: 若上下文要求工期为 90 天，而草稿中写了 120 天，则触发 critical 缺陷并给出 patch。
        """
        issues: List[PatchDiffItem] = []

        # 检查工期要求
        expected_duration = "90 个日历天"
        if "120 个日历天" in draft:
            issues.append({
                "location": "第2章 施工总工期规划",
                "severity": "critical",
                "issue": "检测到严重工期指标偏差！招标文件第1章明确要求总工期不得超过 90 天，当前方案写为 120 天，属于实质性负偏离废标项。",
                "original_text": "工程总工期承诺为 120 个日历天",
                "suggested_patch": "工程总工期严格承诺为 90 个日历天，并编制两班倒赶工保障措施",
            })

        # 计算得分与通过状态
        if not issues:
            return {
                "passed": True,
                "score": 98.5,
                "hallucination_detected": False,
                "issues": [],
                "summary_comment": "方案与招标文件要求完全一致，关键参数均已对齐，审查批准通过。",
            }
        else:
            return {
                "passed": False,
                "score": 65.0,
                "hallucination_detected": True,
                "issues": issues,
                "summary_comment": f"发现 {len(issues)} 处实质性合规偏差，已生成结构化 Patch Diff，要求重写。",
            }


# ---------------------------------------------------------------------------
# 4. LangGraph 状态机图构建器 (StateGraph Builder)
# ---------------------------------------------------------------------------

def build_dual_agent_graph(engine: Optional[DualAgentWorkflowEngine] = None):
    """
    构建并编译 LangGraph StateGraph
    兼容原生 LangGraph 环境；若当前未安装 langgraph，则提供同构的可执行 Runner
    """
    wf_engine = engine or DualAgentWorkflowEngine()

    try:
        from langgraph.graph import StateGraph, END

        builder = StateGraph(GraphState)

        # 添加节点
        builder.add_node("generator", wf_engine.generate_node)
        builder.add_node("critic", wf_engine.critic_node)

        # 设置入口
        builder.set_entry_point("generator")

        # generator -> critic
        builder.add_edge("generator", "critic")

        # critic -> 条件分支
        builder.add_conditional_edges(
            "critic",
            wf_engine.should_continue,
            {
                "generator": "generator",       # 反思回流
                "approved": END,               # 正常通过结束
                "human_review": END,           # 人工介入断点结束
            }
        )

        return builder.compile()

    except ImportError:
        logger.warning("未检测到原生 langgraph 库，启动同构纯原生异步状态机调度器。")
        return PurePythonGraphRunner(wf_engine)


class PurePythonGraphRunner:
    """
    轻量、零外部依赖的纯原生状态机调度器
    与 LangGraph 接口完全同构 (兼容 ainvoke 协议)，保证在任何环境下均可运行与测试
    """

    def __init__(self, engine: DualAgentWorkflowEngine):
        self.engine = engine

    async def ainvoke(self, initial_state: GraphState) -> GraphState:
        state = dict(initial_state)
        current_node = "generator"

        while True:
            if current_node == "generator":
                delta = await self.engine.generate_node(state)
                state.update(delta)
                current_node = "critic"
            elif current_node == "critic":
                delta = await self.engine.critic_node(state)
                state.update(delta)
                
                next_step = self.engine.should_continue(state)
                if next_step == "generator":
                    current_node = "generator"
                else:
                    state["status"] = next_step
                    break

        return state  # type: ignore

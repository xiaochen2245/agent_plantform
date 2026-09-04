"""
工作流路由与 2 次迭代熔断控制器 (Feature 26)
严格限制最大反思重写轮次为 2 次，超限触发 Human-in-the-loop 人工干预
"""

import logging
from typing import Literal

from app.models.audit_rag import TaskStatus
from app.workflow.contracts import GraphState

logger = logging.getLogger(__name__)


class WorkflowRouter:
    """状态机流转条件路由与熔断控制器"""

    @staticmethod
    def should_continue(state: GraphState) -> Literal["generator", "approved", "human_review"]:
        """
        条件流转分支逻辑:
        1. 若 Critic 判定 passed is True -> "approved" (流转至 END 终态，标记 SUCCESS)
        2. 若未通过且 iteration_count < max_iterations (严格 <= 2) -> "generator" (反思回写)
        3. 若未通过且 iteration_count >= max_iterations -> "human_review" (熔断保护挂起)
        """
        feedback = state.get("audit_feedback")
        iteration = state.get("iteration_count", 0)
        # 防御性截断，硬性最大 2 轮反思
        raw_max = state.get("max_iterations", 2)
        max_iter = min(max(raw_max, 1), 2)

        # 分支 1: 审核通过
        if feedback and feedback.get("passed", False):
            logger.info(
                f"[WorkflowRouter] 校核通过 (得分={feedback.get('score')})，工作流进入 approved 终态。"
            )
            return "approved"

        # 分支 2: 未通过且重写次数未达上限 -> 触发反思回流
        if iteration < max_iter:
            logger.warning(
                f"[WorkflowRouter] 校核未通过，当前轮次 {iteration}/{max_iter}，回流至 Generator 进行 Patch Diff 修订。"
            )
            return "generator"

        # 分支 3: 达到或超过 2 次熔断阈值 -> 强行阻断死循环
        logger.error(
            f"[WorkflowRouter] 达到最大迭代轮次 {max_iter} 熔断阈值 (当前 iteration={iteration})，"
            f"触发熔断器保护，状态机流转至 human_review。"
        )
        return "human_review"

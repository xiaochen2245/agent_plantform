"""
LangGraph StateGraph 状态机图构建、PostgresSaver 持久化与执行引擎 (Features 23, 26, 27)
支持真实 LangGraph 与生产级纯原生同构 DualAgentWorkflowRunner 双模运行
"""

import asyncio
from dataclasses import dataclass
import logging
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.models.audit_rag import TaskStatus
from app.workflow.contracts import GraphState
from app.workflow.critic import CriticAgent
from app.workflow.generator import GeneratorAgent
from app.workflow.router import WorkflowRouter

logger = logging.getLogger(__name__)


class PurePythonStateCheckpointer:
    """
    零外部依赖的轻量线程安全异步 Checkpointer
    用于本地开发、测试与离线环境快照持久化
    """

    def __init__(self):
        self._storage: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def aget(self, thread_id: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            state = self._storage.get(thread_id)
            return dict(state) if state is not None else None

    async def aput(self, thread_id: str, state: Dict[str, Any]) -> None:
        async with self._lock:
            self._storage[thread_id] = dict(state)

    def get(self, thread_id: str) -> Optional[Dict[str, Any]]:
        state = self._storage.get(thread_id)
        return dict(state) if state is not None else None

    async def alist(self) -> List[str]:
        async with self._lock:
            return list(self._storage.keys())

    def clear(self) -> None:
        self._storage.clear()


# 全局默认内存 Checkpointer 实例 (保证测试与跨调用线程共享状态)
_DEFAULT_CHECKPOINTER = PurePythonStateCheckpointer()


def get_workflow_checkpointer(db_url: Optional[str] = None):
    """
    Checkpointer 工厂函数:
    - 生产环境 (PostgreSQL): 若安装了 langgraph.checkpoint.postgres，返回 AsyncPostgresSaver
    - 开发/测试环境 (SQLite / 内存): 返回 MemorySaver 或 PurePythonStateCheckpointer
    - 零外部依赖环境: 返回内置同构 PurePythonStateCheckpointer
    """
    database_url = db_url or getattr(settings, "DATABASE_URL", "sqlite+aiosqlite:///:memory:")

    try:
        if "postgres" in database_url:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            logger.info("[Checkpointer] 初始化 PostgreSQL 异步 Checkpointer (AsyncPostgresSaver)")
            return AsyncPostgresSaver.from_conn_string(database_url)
        else:
            from langgraph.checkpoint.memory import MemorySaver
            logger.info("[Checkpointer] 初始化测试环境 MemorySaver Checkpointer")
            return MemorySaver()
    except (ImportError, Exception) as exc:
        logger.warning(f"[Checkpointer] 使用纯原生同构 Checkpointer (原因: {exc})")
        return _DEFAULT_CHECKPOINTER


@dataclass
class SnapshotState:
    """与 LangGraph StateSnapshot 兼容的只读结构"""
    values: Dict[str, Any]


class DualAgentWorkflowRunner:
    """
    纯原生同构双智能体状态机调度器 (Isomorphic LangGraph Runner)
    与 LangGraph 接口完全同构:
    - 支持 ainvoke(state, config)
    - 支持 aget_state(config)
    - 支持 aupdate_state(config, values)
    - 支持 interrupt_before 状态挂起与 Checkpointer 快照存取
    """

    def __init__(
        self,
        generator: GeneratorAgent,
        critic: CriticAgent,
        checkpointer: Any,
    ):
        self.generator = generator
        self.critic = critic
        self.checkpointer = checkpointer

    async def aget_state(self, config: Dict[str, Any]) -> SnapshotState:
        """获取当前会话线程的图状态快照"""
        thread_id = config.get("configurable", {}).get("thread_id")
        if not thread_id:
            raise ValueError("config 必须包含 configurable.thread_id")

        if hasattr(self.checkpointer, "aget"):
            data = await self.checkpointer.aget(thread_id)
        elif hasattr(self.checkpointer, "get"):
            data = self.checkpointer.get(thread_id)
        else:
            data = None

        if data is None:
            raise ValueError(f"未找到 thread_id={thread_id} 的持久化状态快照")
        return SnapshotState(values=dict(data))

    async def aupdate_state(self, config: Dict[str, Any], values: Dict[str, Any]) -> None:
        """更新图状态快照"""
        thread_id = config.get("configurable", {}).get("thread_id")
        if not thread_id:
            raise ValueError("config 必须包含 configurable.thread_id")

        if hasattr(self.checkpointer, "aput"):
            await self.checkpointer.aput(thread_id, values)
        elif hasattr(self.checkpointer, "put"):
            self.checkpointer.put(thread_id, values)

    async def ainvoke(
        self, initial_state: GraphState, config: Optional[Dict[str, Any]] = None
    ) -> GraphState:
        """异步执行双智能体闭环反思状态机"""
        state = dict(initial_state)
        conf = config or {}
        thread_id = (
            conf.get("configurable", {}).get("thread_id")
            or state.get("thread_id")
            or state.get("task_id")
            or "default_thread"
        )
        state["thread_id"] = thread_id

        # 初始化 iteration_count
        if "iteration_count" not in state:
            state["iteration_count"] = 0
        if "review_history" not in state or state["review_history"] is None:
            state["review_history"] = []

        max_allowed_iterations = min(state.get("max_iterations", 2), 2)
        state["max_iterations"] = max_allowed_iterations

        while True:
            # 1. 执行生成节点 (初稿生成或基于 Patch Diff 的靶向修订)
            gen_delta = await self.generator.generate_node(state)
            state.update(gen_delta)

            # 2. 执行校核节点 (反幻觉核验、工期与参数核对)
            crit_delta = await self.critic.critic_node(state)
            state.update(crit_delta)

            # 3. 轮次累加 (完成一轮完整的生成-校核循环)
            state["iteration_count"] = state.get("iteration_count", 0) + 1

            # 4. 路由与熔断决策
            decision = WorkflowRouter.should_continue(state)

            if decision == "generator":
                # 持久化中间状态快照
                if hasattr(self.checkpointer, "aput"):
                    await self.checkpointer.aput(thread_id, state)
                continue

            elif decision == "approved":
                state["status"] = TaskStatus.SUCCESS
                if hasattr(self.checkpointer, "aput"):
                    await self.checkpointer.aput(thread_id, state)
                break

            elif decision == "human_review":
                # 触发 2 次迭代熔断器！挂起状态机并中断
                state["status"] = TaskStatus.HUMAN_REVIEW
                logger.info(
                    f"[StateGraph] 达到轮次上限熔断，挂起等待人工干预: thread_id={thread_id}"
                )
                if hasattr(self.checkpointer, "aput"):
                    await self.checkpointer.aput(thread_id, state)
                break

        return state  # type: ignore


def build_dual_agent_workflow(
    generator_agent: Optional[GeneratorAgent] = None,
    critic_agent: Optional[CriticAgent] = None,
    checkpointer: Optional[Any] = None,
):
    """
    构建并编译带 Checkpointer 与 HITL 中断点的双智能体状态机
    优先尝试原生 LangGraph 编译；若未安装则返回原生同构 Runner
    """
    gen_agent = generator_agent or GeneratorAgent()
    cri_agent = critic_agent or CriticAgent()
    saver = checkpointer if checkpointer is not None else get_workflow_checkpointer()

    try:
        from langgraph.graph import StateGraph, START, END

        builder = StateGraph(GraphState)

        # 1. 注册核心节点
        builder.add_node("generator_node", gen_agent.generate_node)
        builder.add_node("critic_node", cri_agent.critic_node)

        async def human_review_node(state: GraphState) -> Dict[str, Any]:
            logger.info(
                f"[HITL Breakpoint] 任务挂起等待人工审核，thread_id={state.get('thread_id')}"
            )
            return {"status": TaskStatus.HUMAN_REVIEW}

        builder.add_node("human_review_node", human_review_node)

        # 2. 拓扑连线
        builder.add_edge(START, "generator_node")
        builder.add_edge("generator_node", "critic_node")

        # 3. 条件路由
        def router_edge(state: GraphState) -> str:
            return WorkflowRouter.should_continue(state)

        builder.add_conditional_edges(
            "critic_node",
            router_edge,
            {
                "generator": "generator_node",
                "approved": END,
                "human_review": "human_review_node",
            },
        )

        builder.add_edge("human_review_node", END)

        # 4. 编译带断点的图 (在 human_review_node 前挂起中断)
        compiled_graph = builder.compile(
            checkpointer=saver,
            interrupt_before=["human_review_node"],
        )
        return compiled_graph

    except (ImportError, Exception) as exc:
        logger.info(f"[build_dual_agent_workflow] 启动纯原生同构 DualAgentWorkflowRunner (提示: {exc})")
        return DualAgentWorkflowRunner(gen_agent, cri_agent, saver)

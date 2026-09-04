"""
Human-in-the-loop 人工干预与 resume_workflow 恢复处理器 (Feature 28)
负责挂起工作流的状态恢复、人工纠偏补丁注入与审计入库
"""

import datetime
import logging
from typing import Any, Dict, Literal, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_rag import AuditTask, TaskStatus
from app.workflow.contracts import GraphState
from app.workflow.graph import build_dual_agent_workflow, get_workflow_checkpointer

logger = logging.getLogger(__name__)


async def resume_workflow(
    thread_id: str,
    human_patch: Optional[str] = None,
    decision: Literal["approve", "override_and_finish", "reject"] = "override_and_finish",
    session: Optional[AsyncSession] = None,
    workflow_app: Optional[Any] = None,
    checkpointer: Optional[Any] = None,
) -> GraphState:
    """
    恢复处于 human_review 挂起状态的工作流:
    1. 从 Checkpointer 加载 thread_id 对应的当前图状态快照
    2. 将人工补丁 human_patch 注入草案或替换缺陷
    3. 更新审计追踪历史与状态 (SUCCESS 或 FAILED)
    4. 将更新持久化回 Checkpointer
    5. 同步写入数据库 AuditTask 实体 (若提供了 session)
    """
    logger.info(
        f"[resume_workflow] 正在恢复工作流: thread_id={thread_id}, decision={decision}, "
        f"human_patch_len={len(human_patch) if human_patch else 0}"
    )

    saver = checkpointer or get_workflow_checkpointer()
    state: Optional[GraphState] = None

    # 1. 尝试从 workflow_app 读取快照
    if workflow_app and hasattr(workflow_app, "aget_state"):
        try:
            config = {"configurable": {"thread_id": thread_id}}
            snapshot = await workflow_app.aget_state(config)
            if snapshot and hasattr(snapshot, "values") and snapshot.values:
                state = dict(snapshot.values)  # type: ignore
        except Exception as e:
            logger.debug(f"[resume_workflow] aget_state 未命中: {e}")

    # 2. 从 checkpointer 读取快照
    if state is None:
        if hasattr(saver, "aget"):
            raw_state = await saver.aget(thread_id)
            if raw_state:
                state = dict(raw_state)  # type: ignore
        elif hasattr(saver, "get"):
            raw_state = saver.get(thread_id)
            if raw_state:
                state = dict(raw_state)  # type: ignore

    if not state:
        raise ValueError(f"未找到 thread_id={thread_id} 的持久化状态快照")

    # 3. 记录人工干预审计事件
    new_history = list(state.get("review_history", []))
    new_history.append({
        "action": "human_intervention",
        "decision": decision,
        "human_patch_provided": bool(human_patch),
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "summary": f"人工审核判定: {decision}" + (f"，已注入 {len(human_patch)} 字符修正补丁" if human_patch else ""),
    })
    state["review_history"] = new_history

    # 4. 根据人工裁决更新草案与状态
    if decision == "reject":
        state["status"] = TaskStatus.FAILED
    else:
        # 特批放行或提供人工替换补丁
        if human_patch:
            state["draft"] = human_patch
            state["human_patch"] = human_patch
        state["status"] = TaskStatus.SUCCESS

    # 5. 持久化回保存器
    if workflow_app and hasattr(workflow_app, "aupdate_state"):
        try:
            await workflow_app.aupdate_state({"configurable": {"thread_id": thread_id}}, state)
        except Exception as e:
            logger.debug(f"[resume_workflow] aupdate_state 失败或不支持: {e}")

    if hasattr(saver, "aput"):
        await saver.aput(thread_id, state)
    elif hasattr(saver, "put"):
        saver.put(thread_id, state)

    # 6. 同步至数据库 AuditTask (若提供 session)
    if session is not None:
        task_id = state.get("task_id") or thread_id
        try:
            stmt = select(AuditTask).where(AuditTask.id == task_id)
            res = await session.execute(stmt)
            task_obj = res.scalar_one_or_none()
            if task_obj:
                task_obj.status = state["status"]
                task_obj.summary_report = (
                    f"工作流经人工干预完成，判定: {decision}。"
                    + (f" 终版草案长度: {len(state.get('draft', ''))} 字符。" if state.get("draft") else "")
                )
                await session.commit()
                logger.info(f"[resume_workflow] 数据库 AuditTask.id={task_id} 状态已更新为 {task_obj.status}")
        except Exception as exc:
            logger.error(f"[resume_workflow] 更新数据库 AuditTask 异常: {exc}")

    logger.info(f"[resume_workflow] 成功完成恢复流转，终态 status={state['status']}")
    return state

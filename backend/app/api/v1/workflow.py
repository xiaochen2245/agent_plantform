"""
双智能体反思工作流 API 路由 (Workflow API)
提供:
1. POST /api/v1/workflow/run: 触发闭环方案生成与校核 (同步执行或异步 Celery 调度)
2. GET /api/v1/workflow/{task_id}/state: 获取工作流当前状态、图快照与多轮审查历史
3. POST /api/v1/workflow/{task_id}/resume: HITL 人工干预断点恢复与专家特批放行
4. GET /api/v1/workflow/{task_id}/stream: SSE (Server-Sent Events) 实时推送状态机跃迁与 Patch Diff
"""

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_tenant_db, get_tenant_id
from app.celery_app import run_workflow_task
from app.models.audit_rag import AuditTask, Document, TaskStatus, TaskType, Tenant
from app.schemas.gateway import (
    WorkflowResumeRequest,
    WorkflowResumeResponse,
    WorkflowRunRequest,
    WorkflowRunResponse,
    WorkflowStateResponse,
)
from app.workflow.contracts import AuditFeedbackSchema, GraphState
from app.workflow.critic import CriticAgent
from app.workflow.graph import build_dual_agent_workflow, get_workflow_checkpointer
from app.workflow.hitl import resume_workflow
from app.workflow.risk_warning import ProjectRiskInterceptor

logger = logging.getLogger(__name__)

router = APIRouter()


async def _ensure_tenant(session: AsyncSession, tenant_id: str) -> None:
    tenant = await session.get(Tenant, tenant_id)
    if not tenant:
        tenant = Tenant(id=tenant_id, code=tenant_id, name=f"Enterprise {tenant_id}")
        session.add(tenant)
        await session.flush()


@router.post("/run", response_model=WorkflowRunResponse, status_code=status.HTTP_200_OK)
async def run_workflow_endpoint(
    req: WorkflowRunRequest,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
) -> WorkflowRunResponse:
    """
    发起双智能体闭环方案生成与校核。
    - 若提供 project_charter，自动激活前置风险拦截并注入防护栏提示词；
    - 若 async_mode=True，派发至 Celery 异步长任务队列；
    - 若 async_mode=False，同步执行并在 2 次迭代内完成或安全熔断挂起。
    """
    await _ensure_tenant(db, tenant_id)

    task_id = f"task_wf_{uuid.uuid4().hex[:12]}"
    thread_id = req.thread_id or f"th_{uuid.uuid4().hex[:8]}"

    # 1. 前置风险拦截与防护栏提取
    risk_guardrails: Optional[str] = None
    if req.project_charter:
        interceptor = ProjectRiskInterceptor()
        risk_report = await interceptor.intercept_project_risks(
            session=db,
            tenant_id=tenant_id,
            charter=req.project_charter,
        )
        risk_guardrails = risk_report.guardrail_system_prompt_snippet

    # 2. 创建关联虚拟文档以满足 AuditTask 外键完整性
    req_hash = hashlib.sha256(req.rfp_requirements.encode("utf-8")).hexdigest()
    vdoc_id = f"doc_wf_{uuid.uuid4().hex[:10]}"
    vdoc = Document(
        id=vdoc_id,
        tenant_id=tenant_id,
        title=f"Workflow RFP: {thread_id}",
        file_type="txt",
        file_size_bytes=len(req.rfp_requirements.encode("utf-8")),
        s3_path=f"virtual://{tenant_id}/{vdoc_id}",
        file_hash=req_hash,
        parse_status="success",
        doc_ast={},
        doc_metadata={"rfp_requirements_len": len(req.rfp_requirements)},
    )
    db.add(vdoc)
    await db.flush()

    # 3. 异步 Celery 派发分支
    if req.async_mode:
        audit_task = AuditTask(
            id=task_id,
            tenant_id=tenant_id,
            task_type=TaskType.DUAL_AGENT_GENERATION,
            status=TaskStatus.PROCESSING,
            source_document_id=vdoc_id,
            task_config={
                "thread_id": thread_id,
                "rfp_requirements": req.rfp_requirements,
                "max_iterations": req.max_iterations,
            },
            summary_report={"async": True, "thread_id": thread_id},
        )
        db.add(audit_task)
        await db.commit()

        run_workflow_task.delay(
            task_id=task_id,
            tenant_id=tenant_id,
            thread_id=thread_id,
            rfp_requirements=req.rfp_requirements,
            context_chunks=req.context_chunks,
            risk_guardrails=risk_guardrails,
            max_iterations=req.max_iterations,
        )

        return WorkflowRunResponse(
            task_id=task_id,
            thread_id=thread_id,
            tenant_id=tenant_id,
            status=TaskStatus.PROCESSING,
            is_async=True,
            draft=None,
            audit_feedback=None,
            iteration_count=0,
            max_iterations=req.max_iterations,
            created_at=datetime.now(timezone.utc),
        )

    # 4. 同步就地执行分支
    initial_state: GraphState = {
        "tenant_id": tenant_id,
        "task_id": task_id,
        "thread_id": thread_id,
        "rfp_requirements": req.rfp_requirements,
        "context_chunks": req.context_chunks or [],
        "risk_guardrails": risk_guardrails,
        "draft": "",
        "iteration_count": 0,
        "max_iterations": req.max_iterations,
        "status": TaskStatus.PROCESSING,
        "review_history": [],
    }

    workflow = build_dual_agent_workflow()
    final_state = await workflow.ainvoke(
        initial_state, config={"configurable": {"thread_id": thread_id}}
    )

    final_status = final_state.get("status", TaskStatus.SUCCESS)
    feedback = final_state.get("audit_feedback")

    audit_task = AuditTask(
        id=task_id,
        tenant_id=tenant_id,
        task_type=TaskType.DUAL_AGENT_GENERATION,
        status=final_status,
        source_document_id=vdoc_id,
        task_config={
            "thread_id": thread_id,
            "rfp_requirements": req.rfp_requirements,
            "max_iterations": req.max_iterations,
        },
        summary_report=json.loads(json.dumps({
            "thread_id": thread_id,
            "iteration_count": final_state.get("iteration_count", 0),
            "draft_length": len(final_state.get("draft", "")),
            "feedback": feedback,
            "review_history": final_state.get("review_history", []),
        }, default=str)),
    )
    db.add(audit_task)

    if feedback and feedback.get("issues"):
        results = CriticAgent.to_review_results(
            feedback=feedback,
            tenant_id=tenant_id,
            task_id=task_id,
        )
        db.add_all(results)

    await db.commit()
    await db.refresh(audit_task)

    feedback_schema = None
    if feedback:
        feedback_schema = AuditFeedbackSchema.model_validate(feedback)

    return WorkflowRunResponse(
        task_id=task_id,
        thread_id=thread_id,
        tenant_id=tenant_id,
        status=final_status,
        is_async=False,
        draft=final_state.get("draft"),
        audit_feedback=feedback_schema,
        iteration_count=final_state.get("iteration_count", 0),
        max_iterations=req.max_iterations,
        created_at=audit_task.created_at,
    )


@router.get("/{task_id}/state", response_model=WorkflowStateResponse)
async def get_workflow_state(
    task_id: str,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
) -> WorkflowStateResponse:
    """
    查询工作流状态快照、当前草案方案、审查反馈以及多轮反思历史记录。
    优先从底层 Checkpointer 检索运行时图状态，并与 AuditTask 表核验租户归属。
    """
    stmt = select(AuditTask).where(AuditTask.id == task_id, AuditTask.tenant_id == tenant_id)
    task_obj = (await db.execute(stmt)).scalar_one_or_none()

    thread_id = task_obj.task_config.get("thread_id") if (task_obj and task_obj.task_config) else task_id
    summary = task_obj.summary_report if (task_obj and task_obj.summary_report) else {}

    # 从 Checkpointer 尝试加载完整运行态快照
    saver = get_workflow_checkpointer()
    state: Optional[Dict[str, Any]] = None
    if hasattr(saver, "aget"):
        state = await saver.aget(thread_id)
    elif hasattr(saver, "get"):
        state = saver.get(thread_id)

    if not task_obj and not state:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"工作流任务未找到: task_id={task_id}",
        )

    # 校验租户隔离
    if state and state.get("tenant_id") and state.get("tenant_id") != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"工作流任务未找到: task_id={task_id}",
        )

    draft = state.get("draft") if state else summary.get("draft")
    feedback = state.get("audit_feedback") if state else summary.get("feedback")
    history = state.get("review_history", []) if state else summary.get("review_history", [])
    iteration_count = state.get("iteration_count", 0) if state else summary.get("iteration_count", 0)
    current_status = state.get("status") if state else (task_obj.status if task_obj else TaskStatus.SUCCESS)
    human_patch = state.get("human_patch") if state else None

    feedback_schema = None
    if feedback:
        feedback_schema = AuditFeedbackSchema.model_validate(feedback)

    return WorkflowStateResponse(
        task_id=task_id,
        thread_id=str(thread_id),
        tenant_id=tenant_id,
        status=current_status,
        draft=draft,
        audit_feedback=feedback_schema,
        iteration_count=iteration_count,
        review_history=history,
        human_patch=human_patch,
        updated_at=datetime.now(timezone.utc),
    )


@router.post("/{task_id}/resume", response_model=WorkflowResumeResponse)
async def resume_workflow_endpoint(
    task_id: str,
    req: WorkflowResumeRequest,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
) -> WorkflowResumeResponse:
    """
    针对触发 2 次迭代熔断挂起在 human_review 的任务，注入专家审核决策或纠偏补丁并恢复执行。
    """
    stmt = select(AuditTask).where(AuditTask.id == task_id, AuditTask.tenant_id == tenant_id)
    task_obj = (await db.execute(stmt)).scalar_one_or_none()

    thread_id = task_obj.task_config.get("thread_id") if (task_obj and task_obj.task_config) else task_id

    try:
        resumed_state = await resume_workflow(
            thread_id=str(thread_id),
            decision=req.decision,
            human_patch=req.human_patch,
            session=db,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"恢复失败: {exc}",
        )

    return WorkflowResumeResponse(
        task_id=task_id,
        thread_id=str(thread_id),
        status=resumed_state["status"],
        final_draft=resumed_state.get("draft"),
        decision=req.decision,
        resumed_at=datetime.now(timezone.utc),
    )


@router.get("/{task_id}/stream")
async def stream_workflow_events(
    task_id: str,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
):
    """
    SSE (Server-Sent Events) 端点，以 text/event-stream 协议实时推送工作流状态机状态跃迁、
    行内 Patch Diff 批注以及终态结果。
    """
    stmt = select(AuditTask).where(AuditTask.id == task_id, AuditTask.tenant_id == tenant_id)
    task_obj = (await db.execute(stmt)).scalar_one_or_none()

    thread_id = task_obj.task_config.get("thread_id") if (task_obj and task_obj.task_config) else task_id

    # 加载状态快照
    saver = get_workflow_checkpointer()
    state: Optional[Dict[str, Any]] = None
    if hasattr(saver, "aget"):
        state = await saver.aget(thread_id)
    elif hasattr(saver, "get"):
        state = saver.get(thread_id)

    if not task_obj and not state:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"工作流任务未找到: task_id={task_id}",
        )

    summary = task_obj.summary_report if (task_obj and task_obj.summary_report) else {}
    history = state.get("review_history", []) if state else summary.get("review_history", [])
    feedback = state.get("audit_feedback") if state else summary.get("feedback")
    final_status = state.get("status") if state else (task_obj.status if task_obj else TaskStatus.SUCCESS)
    draft = state.get("draft", "") if state else summary.get("draft", "")

    async def event_generator() -> AsyncGenerator[str, None]:
        # 1. 发送初始启动与草案生成事件
        yield f"event: state_transition\ndata: {json.dumps({'node': 'generator_node', 'status': 'drafting', 'task_id': task_id, 'thread_id': thread_id})}\n\n"
        await asyncio.sleep(0.01)

        # 2. 遍历回放历史审查事件与 Patch Diff
        for idx, event in enumerate(history):
            action = event.get("action")
            yield f"event: state_transition\ndata: {json.dumps({'node': 'critic_node' if 'critic' in str(action) else 'generator_node', 'action': action, 'round': event.get('round', 0)})}\n\n"
            await asyncio.sleep(0.01)

            # 若存在局部补丁，逐项推送
            if "feedback" in event and isinstance(event["feedback"], dict):
                fb = event["feedback"]
                for patch in fb.get("issues", []):
                    yield f"event: patch_diff\ndata: {json.dumps(patch, ensure_ascii=False)}\n\n"
                    await asyncio.sleep(0.01)

        # 3. 推送最新审计反馈
        if feedback:
            yield f"event: audit_feedback\ndata: {json.dumps(feedback, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.01)

        # 4. 推送终态事件 (工作流完成或触发熔断人工介入)
        if final_status in (TaskStatus.HUMAN_REVIEW, "human_review"):
            yield f"event: human_review_required\ndata: {json.dumps({'task_id': task_id, 'thread_id': thread_id, 'status': 'human_review', 'reason': '2-iteration circuit breaker limit reached'})}\n\n"
        else:
            yield f"event: workflow_complete\ndata: {json.dumps({'task_id': task_id, 'thread_id': thread_id, 'status': str(final_status), 'draft_length': len(draft)})}\n\n"

    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)

"""
单元测试: Human-in-the-loop (HITL) 人工干预与 resume_workflow 恢复执行 (Feature 28)
验证工作流在 human_review 挂起后状态持久化与人工补丁注入恢复终态
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models.audit_rag import AuditTask, Document, TaskStatus, TaskType, Tenant
from app.workflow.contracts import GraphState
from app.workflow.graph import PurePythonStateCheckpointer, build_dual_agent_workflow
from app.workflow.hitl import resume_workflow


@pytest.mark.asyncio
async def test_hitl_checkpoint_and_resume_with_human_patch():
    """验证 Checkpointer 状态持久化与人工补丁注入恢复流转至 SUCCESS"""
    checkpointer = PurePythonStateCheckpointer()
    thread_id = "th_hitl_001"

    # 模拟进入 human_review 挂起状态的初始快照
    suspended_state: GraphState = {
        "tenant_id": "tenant_alpha",
        "task_id": "task_hitl_01",
        "thread_id": thread_id,
        "draft": "未解决工期矛盾的草案（承诺120天）",
        "status": TaskStatus.HUMAN_REVIEW,
        "iteration_count": 2,
        "max_iterations": 2,
        "review_history": [
            {"iteration": 2, "action": "circuit_breaker_triggered"}
        ],
    }

    # 持久化到 Checkpointer
    await checkpointer.aput(thread_id, suspended_state)

    # 验证 Checkpointer 能够正确读取
    stored_state = await checkpointer.aget(thread_id)
    assert stored_state is not None
    assert stored_state["status"] == TaskStatus.HUMAN_REVIEW

    # 执行人工介入恢复: 注入人工核准补丁
    human_correction = (
        "【人工特批终版】第2章 施工总工期规划：工程总工期经造价与施工处特批调整为 90 个日历天。"
    )
    resumed_state = await resume_workflow(
        thread_id=thread_id,
        human_patch=human_correction,
        decision="override_and_finish",
        checkpointer=checkpointer,
    )

    # 验证流转状态变为 SUCCESS
    assert resumed_state["status"] == TaskStatus.SUCCESS
    assert resumed_state["draft"] == human_correction
    assert resumed_state["human_patch"] == human_correction

    # 验证审计追踪
    history = resumed_state["review_history"]
    intervention = next((h for h in history if h.get("action") == "human_intervention"), None)
    assert intervention is not None
    assert intervention["decision"] == "override_and_finish"
    assert intervention["human_patch_provided"] is True


@pytest.mark.asyncio
async def test_hitl_resume_reject():
    """验证人工审核判定驳回 (reject) 状态流转至 FAILED"""
    checkpointer = PurePythonStateCheckpointer()
    thread_id = "th_hitl_002"

    suspended_state: GraphState = {
        "tenant_id": "tenant_alpha",
        "task_id": "task_hitl_02",
        "thread_id": thread_id,
        "draft": "严重违规方案",
        "status": TaskStatus.HUMAN_REVIEW,
        "iteration_count": 2,
        "review_history": [],
    }
    await checkpointer.aput(thread_id, suspended_state)

    resumed_state = await resume_workflow(
        thread_id=thread_id,
        decision="reject",
        checkpointer=checkpointer,
    )

    assert resumed_state["status"] == TaskStatus.FAILED
    assert resumed_state["draft"] == "严重违规方案"


@pytest.mark.asyncio
async def test_hitl_resume_db_synchronization():
    """验证 resume_workflow 能够同步更新 SQLAlchemy 数据库中 AuditTask 的实体状态"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    checkpointer = PurePythonStateCheckpointer()

    thread_id = "th_db_sync_003"
    task_id = "audit_task_db_003"

    async with session_factory() as session:
        # 创建测试租户与待审核任务及前置文档
        tenant = Tenant(id="tenant_db_test", code="T_DB", name="测试租户")
        doc = Document(
            id="doc_hitl_001",
            tenant_id="tenant_db_test",
            title="工程技术标.docx",
            file_type="docx",
            s3_path="/s3/tenant_db_test/doc.docx",
            file_hash="hash_hitl_123",
        )
        task = AuditTask(
            id=task_id,
            tenant_id="tenant_db_test",
            source_document_id="doc_hitl_001",
            task_type=TaskType.DUAL_AGENT_GENERATION,
            status=TaskStatus.HUMAN_REVIEW,
            summary_report="等待人工干预",
        )
        session.add_all([tenant, doc, task])
        await session.commit()

        # Checkpointer 状态
        state: GraphState = {
            "tenant_id": "tenant_db_test",
            "task_id": task_id,
            "thread_id": thread_id,
            "draft": "草案",
            "status": TaskStatus.HUMAN_REVIEW,
            "iteration_count": 2,
            "review_history": [],
        }
        await checkpointer.aput(thread_id, state)

        # 恢复工作流并同步 session
        resumed = await resume_workflow(
            thread_id=thread_id,
            human_patch="合规终版草案",
            decision="approve",
            session=session,
            checkpointer=checkpointer,
        )
        assert resumed["status"] == TaskStatus.SUCCESS

        # 验证数据库中 AuditTask 状态被成功刷新
        refreshed = await session.get(AuditTask, task_id)
        assert refreshed is not None
        assert refreshed.status == TaskStatus.SUCCESS
        assert "人工干预完成" in refreshed.summary_report

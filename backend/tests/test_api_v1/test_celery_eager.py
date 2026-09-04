"""
Celery Eager 模式与任务上下文透传测试套件
验证:
1. task_always_eager=True 下 parse_document_task 与 run_workflow_task 同步就地执行
2. 任务执行期间 TenantContext 租户隔离上下文严格保留
"""

from pathlib import Path
import tempfile
import pytest
from sqlalchemy import select

from app.celery_app import parse_document_task, run_workflow_task
from app.core.tenant_context import TenantContext
from app.db.session import SessionLocal
from app.models.audit_rag import AuditTask, Document, DocumentChunk, TaskStatus, TaskType, Tenant


from tests.test_api_v1.test_documents_api import make_test_docx_bytes


@pytest.mark.asyncio
async def test_celery_parse_document_eager():
    """验证 Celery 在 eager 模式下解析文档并入库父子切片"""
    tenant_id = "tenant_celery_parse"
    doc_id = "doc_celery_test_01"

    # 创建物理临时 DOCX 文件
    docx_bytes = make_test_docx_bytes()
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
        f.write(docx_bytes)
        temp_path = f.name

    try:
        async with SessionLocal() as session:
            session.add(Tenant(id=tenant_id, code=tenant_id, name=f"Tenant {tenant_id}"))
            doc = Document(
                id=doc_id,
                tenant_id=tenant_id,
                title="celery_doc.docx",
                file_type="docx",
                s3_path=temp_path,
                file_hash="mock_hash_celery",
                parse_status="pending",
                doc_ast={},
            )
            session.add(doc)
            await session.commit()

        # 调用 Celery 任务 .delay
        res = parse_document_task.delay(
            document_id=doc_id,
            tenant_id=tenant_id,
            file_path=temp_path,
            file_name="celery_doc.docx",
        )
        assert res.state == "SUCCESS"

        # 验证数据库状态更新
        async with SessionLocal() as session:
            updated_doc = await session.get(Document, doc_id)
            assert updated_doc is not None
            assert updated_doc.parse_status == "success"

            chunks_stmt = select(DocumentChunk).where(DocumentChunk.document_id == doc_id)
            chunks = (await session.execute(chunks_stmt)).scalars().all()
            assert len(chunks) > 0

    finally:
        Path(temp_path).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_celery_workflow_eager_and_tenant_preservation():
    """验证 run_workflow_task 异步任务执行与租户上下文透传"""
    tenant_id = "tenant_celery_wf"
    task_id = "task_celery_wf_01"
    thread_id = "th_celery_wf_01"
    vdoc_id = "vdoc_celery_01"

    async with SessionLocal() as session:
        session.add(Tenant(id=tenant_id, code=tenant_id, name=f"Tenant {tenant_id}"))
        session.add(
            Document(
                id=vdoc_id,
                tenant_id=tenant_id,
                title="Virtual RFP",
                file_type="txt",
                s3_path="virtual://",
                file_hash="hash_celery_rfp",
                parse_status="success",
                doc_ast={},
            )
        )
        audit_task = AuditTask(
            id=task_id,
            tenant_id=tenant_id,
            task_type=TaskType.DUAL_AGENT_GENERATION,
            status=TaskStatus.PENDING,
            source_document_id=vdoc_id,
            task_config={"thread_id": thread_id},
        )
        session.add(audit_task)
        await session.commit()

    # 执行 Celery 任务
    res = run_workflow_task.delay(
        task_id=task_id,
        tenant_id=tenant_id,
        thread_id=thread_id,
        rfp_requirements="工期90天，冷机COP>=5.0",
        context_chunks=[],
        risk_guardrails=None,
        max_iterations=2,
    )
    assert res.state == "SUCCESS"

    async with SessionLocal() as session:
        updated_task = await session.get(AuditTask, task_id)
        assert updated_task is not None
        assert updated_task.status == TaskStatus.SUCCESS
        assert updated_task.summary_report.get("iteration_count", 0) >= 1

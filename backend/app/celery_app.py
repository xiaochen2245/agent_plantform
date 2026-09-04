"""
Celery 分布式任务队列主配置与核心任务定义
包含:
1. 生产级 Celery 实例与配置
2. 自动化测试/本地降级模式 (task_always_eager=True 与 memory:// broker)
3. 线程安全协程执行器 (run_async)
4. 文档异步解析入库任务 (parse_document_task)
5. 双智能体长工作流后台执行任务 (run_workflow_task)
6. 严格的多租户上下文透传与 PostgreSQL 16+ RLS 激活
"""

import asyncio
import concurrent.futures
import json
import logging
import os
from pathlib import Path
from typing import Any, Coroutine, Dict, List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# 判断是否启用 eager 模式 (测试环境或显式环境变量配置)
_env_eager = os.getenv("CELERY_TASK_ALWAYS_EAGER", "").lower() in ("true", "1", "yes")
IS_EAGER = _env_eager or getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)

try:
    from celery import Celery

    celery_app = Celery(
        "rag_workflow_worker",
        broker="memory://" if IS_EAGER else getattr(settings, "CELERY_BROKER_URL", "redis://localhost:6379/0"),
        backend="cache+memory://" if IS_EAGER else getattr(settings, "CELERY_RESULT_BACKEND", "redis://localhost:6379/0"),
    )

    celery_app.conf.update(
        task_always_eager=IS_EAGER,
        task_eager_propagates=True,
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
        task_time_limit=3600,
        worker_prefetch_multiplier=1,
    )

except ImportError:
    # 极简离线降级 MockCeleryApp
    class MockAsyncResult:
        def __init__(self, task_id: str, result: Any = None):
            self.id = task_id
            self.state = "SUCCESS"
            self.result = result

        def get(self, timeout=None):
            return self.result

    class MockTask:
        def __init__(self, func):
            self.func = func
            self.__name__ = func.__name__

        def delay(self, *args, **kwargs):
            res = self.func(*args, **kwargs)
            return MockAsyncResult(task_id="mock_task_id", result=res)

        def apply_async(self, args=None, kwargs=None, **opts):
            args = args or ()
            kwargs = kwargs or {}
            res = self.func(*args, **kwargs)
            return MockAsyncResult(task_id="mock_task_id", result=res)

        def __call__(self, *args, **kwargs):
            return self.func(*args, **kwargs)

    class MockCeleryApp:
        conf = {"task_always_eager": True}

        def task(self, *dargs, **dkwargs):
            def decorator(fn):
                return MockTask(fn)
            return decorator

        def autodiscover_tasks(self, *args, **kwargs):
            pass

    celery_app = MockCeleryApp()
    logger.warning("未检测到 Celery 库，回退至轻量 MockCeleryApp 同步执行器")


def run_async(coro: Coroutine) -> Any:
    """
    在 Celery 同步 Worker 线程或测试环境中安全同步执行异步协程。
    针对当前线程已存在运行中事件循环的情况（如 FastAPI 异步路由内部触发 eager 任务），
    使用独立的线程池运行以防止事件循环冲突死锁。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()
    else:
        return asyncio.run(coro)


# ============================================================================
# 异步业务逻辑实现
# ============================================================================

async def _async_parse_document(
    document_id: str,
    tenant_id: str,
    file_path: str,
    file_name: str,
) -> Dict[str, Any]:
    """文档异步解析、切片、向量化与入库核心逻辑"""
    from app.core.tenant_context import TenantContext, apply_tenant_rls_session
    from app.db.session import SessionLocal
    from app.models.audit_rag import Document
    from app.parsers.factory import parser_factory
    from app.rag.chunker import ParentChildChunker
    from app.rag.embedding import get_embedding_service

    with TenantContext(tenant_id):
        async with SessionLocal() as session:
            await apply_tenant_rls_session(session, tenant_id)
            doc = await session.get(Document, document_id)
            if not doc:
                logger.error(f"[parse_document_task] 文档未找到: id={document_id}, tenant={tenant_id}")
                return {"status": "error", "message": "Document not found"}

            doc.parse_status = "parsing"
            await session.commit()

            try:
                p = Path(file_path)
                if not p.exists():
                    raise FileNotFoundError(f"物理文档文件不存在: {file_path}")
                content = p.read_bytes()

                # 1. 解析为 UnifiedDocumentAST
                ast = await parser_factory.parse_document(
                    content=content,
                    file_name=file_name,
                    tenant_id=tenant_id,
                    document_id=document_id,
                )

                # 2. 父子层级切片
                chunker = ParentChildChunker()
                chunks = chunker.chunk_document_ast(ast)

                # 3. 向量化嵌入 (Child / Table 切片)
                emb_service = get_embedding_service()
                await emb_service.embed_chunks(chunks)

                # 4. 更新文档状态与持久化切片
                doc.doc_ast = ast.model_dump()
                doc.parse_status = "success"
                meta = dict(doc.doc_metadata or {})
                meta.update({
                    "node_count": len(ast.nodes),
                    "chunk_count": len(chunks),
                    "file_name": file_name,
                })
                doc.doc_metadata = meta

                session.add_all(chunks)
                await session.commit()

                logger.info(
                    f"[parse_document_task] 解析成功: doc_id={document_id}, nodes={len(ast.nodes)}, chunks={len(chunks)}"
                )
                return {
                    "status": "success",
                    "document_id": document_id,
                    "chunks_count": len(chunks),
                    "ast_node_count": len(ast.nodes),
                }

            except Exception as exc:
                logger.exception(f"[parse_document_task] 解析失败: {exc}")
                doc.parse_status = "failed"
                meta = dict(doc.doc_metadata or {})
                meta["error_message"] = str(exc)
                doc.doc_metadata = meta
                await session.commit()
                return {"status": "failed", "document_id": document_id, "error": str(exc)}


async def _async_run_workflow(
    task_id: str,
    tenant_id: str,
    thread_id: str,
    rfp_requirements: str,
    context_chunks: Optional[List[Dict[str, Any]]] = None,
    risk_guardrails: Optional[Any] = None,
    max_iterations: int = 2,
) -> Dict[str, Any]:
    """双智能体工作流后台执行、状态机流转与审计入库"""
    from app.core.tenant_context import TenantContext, apply_tenant_rls_session
    from app.db.session import SessionLocal
    from app.models.audit_rag import AuditTask, TaskStatus
    from app.workflow.contracts import GraphState
    from app.workflow.critic import CriticAgent
    from app.workflow.graph import build_dual_agent_workflow

    with TenantContext(tenant_id):
        async with SessionLocal() as session:
            await apply_tenant_rls_session(session, tenant_id)
            task_obj = await session.get(AuditTask, task_id)
            if task_obj:
                task_obj.status = TaskStatus.PROCESSING
                await session.commit()

            initial_state: GraphState = {
                "tenant_id": tenant_id,
                "task_id": task_id,
                "thread_id": thread_id,
                "rfp_requirements": rfp_requirements,
                "context_chunks": context_chunks or [],
                "risk_guardrails": risk_guardrails,
                "draft": "",
                "iteration_count": 0,
                "max_iterations": max_iterations,
                "status": TaskStatus.PROCESSING,
                "review_history": [],
            }

            workflow = build_dual_agent_workflow()
            final_state = await workflow.ainvoke(
                initial_state, config={"configurable": {"thread_id": thread_id}}
            )

            if task_obj:
                task_obj.status = final_state.get("status", TaskStatus.SUCCESS)
                task_obj.summary_report = json.loads(json.dumps({
                    "iteration_count": final_state.get("iteration_count", 0),
                    "draft_length": len(final_state.get("draft", "")),
                    "feedback": final_state.get("audit_feedback"),
                }, default=str))
                feedback = final_state.get("audit_feedback")
                if feedback and feedback.get("issues"):
                    results = CriticAgent.to_review_results(
                        feedback=feedback,
                        tenant_id=tenant_id,
                        task_id=task_id,
                    )
                    session.add_all(results)
                await session.commit()

            logger.info(
                f"[run_workflow_task] 工作流执行完成: task_id={task_id}, status={final_state.get('status')}"
            )
            return {
                "status": str(final_state.get("status")),
                "task_id": task_id,
                "iteration_count": final_state.get("iteration_count", 0),
                "draft": final_state.get("draft", ""),
            }


# ============================================================================
# Celery 任务包装声明
# ============================================================================

@celery_app.task(name="app.tasks.document_tasks.parse_document_task", bind=True)
def parse_document_task(self, document_id: str, tenant_id: str, file_path: str, file_name: str) -> Dict[str, Any]:
    """文档解析 Celery 任务"""
    return run_async(_async_parse_document(document_id, tenant_id, file_path, file_name))


@celery_app.task(name="app.tasks.workflow_tasks.run_workflow_task", bind=True)
def run_workflow_task(
    self,
    task_id: str,
    tenant_id: str,
    thread_id: str,
    rfp_requirements: str,
    context_chunks: Optional[List[Dict[str, Any]]] = None,
    risk_guardrails: Optional[Any] = None,
    max_iterations: int = 2,
) -> Dict[str, Any]:
    """双智能体工作流执行 Celery 任务"""
    return run_async(
        _async_run_workflow(
            task_id=task_id,
            tenant_id=tenant_id,
            thread_id=thread_id,
            rfp_requirements=rfp_requirements,
            context_chunks=context_chunks,
            risk_guardrails=risk_guardrails,
            max_iterations=max_iterations,
        )
    )

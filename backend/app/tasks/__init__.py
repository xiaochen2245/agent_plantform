"""
Celery 异步任务包
"""
from app.celery_app import parse_document_task, run_workflow_task

__all__ = ["parse_document_task", "run_workflow_task"]

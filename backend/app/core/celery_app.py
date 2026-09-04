"""
Re-export of Celery app and tasks for core path compatibility.
"""
from app.celery_app import (
    celery_app,
    IS_EAGER,
    parse_document_task,
    run_async,
    run_workflow_task,
)

__all__ = [
    "celery_app",
    "IS_EAGER",
    "run_async",
    "parse_document_task",
    "run_workflow_task",
]

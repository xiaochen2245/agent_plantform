"""RAGFlow 依赖注入：进程级单例（app.state.ragflow，lifespan 管理）。"""
from fastapi import Request

from app.ragflow.client import RagflowClient


def get_ragflow(request: Request) -> RagflowClient:
    client: RagflowClient = request.app.state.ragflow
    return client

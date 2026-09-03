"""RAGFlow 依赖注入：按登录用户部门解析租户绑定 → per-tenant 客户端。

客户端缓存于 app.state.ragflow_clients（token → RagflowClient），
lifespan 关闭时统一 aclose。无绑定/禁用 → 503（不给后备 key 兜底，
租户边界优先；后备 key 仅留给纯运维脚本）。
"""
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.core.vault import decrypt
from app.db.session import get_db
from app.models.ragflow_binding import RagflowBinding
from app.models.user import User
from app.ragflow.client import RagflowClient


def _client_for_token(request: Request, token: str) -> RagflowClient:
    """测试可通过 monkeypatch 此函数注入假身。"""
    cache: dict[str, RagflowClient] = request.app.state.ragflow_clients
    client = cache.get(token)
    if client is None:
        client = RagflowClient(api_key=token)
        cache[token] = client
    return client


async def get_ragflow(
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> RagflowClient:
    if user.dept_id is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "no department binding for rag engine",
        )
    binding = await db.scalar(
        select(RagflowBinding).where(
            RagflowBinding.department_id == user.dept_id,
            RagflowBinding.status == "active",
        )
    )
    if binding is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "no rag binding for your department",
        )
    return _client_for_token(request, decrypt(binding.ragflow_api_token_enc))


async def close_ragflow_clients(request: Request) -> None:
    for client in request.app.state.ragflow_clients.values():
        await client.aclose()
    request.app.state.ragflow_clients.clear()

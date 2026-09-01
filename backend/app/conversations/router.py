"""Conversations 路由：GET /api/conversations（仅本人，软删排除，updated_at desc）。"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.db.session import get_db
from app.models.conversation import Conversation
from app.models.user import User
from app.schemas.chat import ConversationsResponse

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.get("", response_model=ConversationsResponse)
async def list_conversations(
    app_id: int | None = None,
    limit: int = 20,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationsResponse:
    q = select(Conversation).where(
        Conversation.user_id == user.id,
        Conversation.deleted_at.is_(None),
    )
    if app_id is not None:
        q = q.where(Conversation.app_id == app_id)
    q = q.order_by(Conversation.updated_at.desc()).limit(max(1, min(limit, 100)))
    rows = (await db.execute(q)).scalars().all()
    return ConversationsResponse(
        items=[
            {
                "id": str(r.id),
                "title": r.title,
                "message_count": r.message_count or 0,
                "updated_at": r.updated_at.isoformat(),
            }
            for r in rows
        ]
    )

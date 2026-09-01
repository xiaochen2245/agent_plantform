"""Conversations 路由：列表 + 消息详情（仅本人，软删排除，updated_at desc）。"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.db.session import get_db
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.schemas.chat import ConversationsResponse, MessagesResponse

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


@router.get("/{conversation_id}/messages", response_model=MessagesResponse)
async def conversation_messages(
    conversation_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> MessagesResponse:
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    conv = await db.get(Conversation, conv_uuid)
    if conv is None or conv.deleted_at is not None or conv.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    rows = (
        await db.execute(
            select(Message)
            .where(Message.conversation_id == conv_uuid)
            .order_by(Message.created_at, Message.id)
        )
    ).scalars().all()

    def _iso(dt) -> str:
        # SQLite 返回 naive datetime，统一补 UTC
        if dt.tzinfo is None:
            import datetime as _dt

            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return dt.isoformat()

    return MessagesResponse(
        messages=[
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "created_at": _iso(m.created_at),
                "files": m.files or [],  # 契约 v4：附件元数据
                "reasoning": m.reasoning,  # 契约 v6：思考过程（null 缺省）
            }
            for m in rows
        ]
    )

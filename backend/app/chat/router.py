"""Chat 路由：POST /api/chat/send（SSE 透传代理）。"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.chat.service import stream_dify_events
from app.db.session import get_db
from app.dify.deps import get_dify
from app.dify.client import DifyClient
from app.models.app import App
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.schemas.chat import ChatSendRequest

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("/send")
async def send_message(
    body: ChatSendRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    dify: DifyClient = Depends(get_dify),
) -> StreamingResponse:
    app_row = await db.get(App, body.app_id)
    if app_row is None or app_row.status != 1:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "App not found")

    if body.conversation_id:
        try:
            conv_id = uuid.UUID(body.conversation_id)
        except ValueError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
        conv = await db.get(Conversation, conv_id)
        if (
            conv is None
            or conv.deleted_at is not None
            or conv.user_id != user.id
            or conv.app_id != app_row.id
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    else:
        conv = Conversation(
            user_id=user.id, app_id=app_row.id, title=body.query[:20]
        )
        db.add(conv)
        await db.flush()

    # 用户消息在开流前落库：即使生成器从未推进，提问也不丢
    db.add(Message(conversation_id=conv.id, role="user", content=body.query))
    conv.message_count = (conv.message_count or 0) + 1
    await db.commit()

    return StreamingResponse(
        stream_dify_events(dify, user, app_row, conv, body.query),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 关掉 nginx 缓冲（设计 §5.3）
        },
    )

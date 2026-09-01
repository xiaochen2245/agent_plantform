"""Chat 路由：POST /api/chat/send（SSE 透传代理；v3 增工作流模式分支）。"""
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.authz import is_authorized
from app.chat.service import stream_dify_events, stream_workflow_events
from app.db.session import get_db
from app.dify.deps import get_dify
from app.dify.client import DifyClient
from app.models.app import App
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.schemas.chat import ChatSendRequest

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _validate_workflow_inputs(schema: list, inputs: dict) -> None:
    """契约 v3：缺必填 → 400 {"detail": "missing required input: <name>"}。"""
    for field in schema or []:
        name = str(field.get("name", ""))
        if field.get("required") and not str(inputs.get(name, "")).strip():
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"missing required input: {name}"
            )


def _workflow_title(schema: list, inputs: dict, query: str) -> str:
    """契约 v3：title 取首个 inputs 值，否则 query 前 20 字。"""
    for field in schema or []:
        value = str(inputs.get(str(field.get("name", "")), "")).strip()
        if value:
            return value[:20]
    return query[:20]


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

    # 授权前置校验（契约 v2：未授权 403，防绕过前端）
    if not await is_authorized(db, user, app_row.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not authorized for this app")

    is_workflow = app_row.mode == "workflow"
    inputs = dict(body.inputs or {})
    if is_workflow:
        _validate_workflow_inputs(app_row.inputs_schema or [], inputs)
    elif not (body.query and body.query.strip()):
        # 契约 v3：chat/agent 模式 query 必填，workflow 模式用 inputs
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "query is required")

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
        title = (
            _workflow_title(app_row.inputs_schema or [], inputs, body.query)
            if is_workflow
            else body.query[:20]
        )
        conv = Conversation(user_id=user.id, app_id=app_row.id, title=title)
        db.add(conv)
        await db.flush()

    # 用户消息在开流前落库：即使生成器从未推进，提问也不丢
    # workflow 模式无 query 时，用 inputs 摘要作为用户消息内容
    user_content = body.query or json.dumps(inputs, ensure_ascii=False)[:8000]
    db.add(Message(conversation_id=conv.id, role="user", content=user_content))
    conv.message_count = (conv.message_count or 0) + 1
    await db.commit()

    generator = (
        stream_workflow_events(dify, user, app_row, conv, inputs)
        if is_workflow
        else stream_dify_events(dify, user, app_row, conv, body.query)
    )
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 关掉 nginx 缓冲（设计 §5.3）
        },
    )

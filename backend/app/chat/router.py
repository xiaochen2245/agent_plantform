"""Chat 路由：POST /api/chat/send（SSE 透传代理；v3 增工作流模式分支；v4 增附件转发）。"""
import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
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
from app.models.upload_file import UploadFile
from app.models.user import User
from app.schemas.chat import ChatSendRequest

router = APIRouter(prefix="/api/chat", tags=["chat"])

_logger = logging.getLogger("app.chat")


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


async def _resolve_files(
    db: AsyncSession, dify: DifyClient, app_row: App, user: User, file_ids: list[str]
) -> tuple[list[dict], list[dict]]:
    """契约 v4：发送时转发 Dify /files/upload 换 dify_file_id。

    - 归属校验：非本人/不存在 → 404（与 conversation 语义一致）
    - 单文件转发失败：跳过并记日志，不阻断消息发送（dify_file_id 置 None）
    返回 (dify_files 参数, message_files 元数据)。
    """
    rows = (
        await db.execute(select(UploadFile).where(UploadFile.id.in_(file_ids)))
    ).scalars().all()
    by_id = {r.id: r for r in rows}

    dify_files: list[dict] = []
    message_files: list[dict] = []
    for fid in dict.fromkeys(file_ids):  # 去重保序
        row = by_id.get(fid)
        if row is None or row.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
        path = Path(row.storage_path)
        if not path.is_file():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")

        uploaded = await dify.upload_file(app_row.id, row.name, path.read_bytes(), row.mime)
        dify_file_id = None
        if uploaded and uploaded.get("id"):
            dify_file_id = str(uploaded["id"])
            dify_files.append(
                {
                    "type": "image" if row.mime.startswith("image/") else "document",
                    "transfer_method": "local_file",
                    "upload_file_id": dify_file_id,
                }
            )
        else:
            _logger.warning("dify upload skipped for file %s", fid)

        message_files.append(
            {
                "file_id": row.id,
                "name": row.name,
                "size": row.size,
                "mime": row.mime,
                "dify_file_id": dify_file_id,
            }
        )
    return dify_files, message_files


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

    # 契约 v4：附件（仅 chat/agent；workflow 模式明确不支持，忽略）
    dify_files: list[dict] = []
    message_files: list[dict] = []
    if body.files and not is_workflow:
        dify_files, message_files = await _resolve_files(
            db, dify, app_row, user, body.files
        )

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
    db.add(
        Message(
            conversation_id=conv.id,
            role="user",
            content=user_content,
            files=message_files or None,  # 契约 v4：[{file_id,name,size,mime,dify_file_id}]
        )
    )
    conv.message_count = (conv.message_count or 0) + 1
    await db.commit()

    generator = (
        stream_workflow_events(dify, user, app_row, conv, inputs)
        if is_workflow
        else stream_dify_events(dify, user, app_row, conv, body.query, dify_files)
    )
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 关掉 nginx 缓冲（设计 §5.3）
        },
    )

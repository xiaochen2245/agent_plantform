"""对话流业务：SSE 逐行透传 + 对话镜像落库（设计 §5.2 / §13.4）。

关键语义：
- 逐行转发 Dify 原文（含空行分隔符），前端看到的事件形态与 Dify 一致
- event=message 累加 answer；message_end 取 metadata.usage 存 token_usage
- finally 块独立会话落库：客户端断流（GeneratorExit）也保证已生成内容入库
- agent_done 只在终端路径（正常/超时/错误）追加；断流路径禁止 yield（GeneratorExit 后再 yield 会 RuntimeError）
"""
import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import SessionLocal
from app.dify.client import DifyClient
from app.models.app import App
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User


def _sse(event: str, data: dict | None = None) -> str:
    payload = json.dumps(data or {}, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


async def _persist_assistant(
    session_factory: async_sessionmaker[AsyncSession],
    conversation_id,
    accumulated: str,
    token_usage: dict | None,
    dify_conversation_id: str | None,
) -> None:
    """流结束后镜像落库；独立会话，不依赖请求作用域。"""
    async with session_factory() as session:
        conv = await session.get(Conversation, conversation_id)
        if conv is None:
            return
        if accumulated:
            session.add(
                Message(conversation_id=conv.id, role="assistant", content=accumulated)
            )
            conv.message_count = (conv.message_count or 0) + 1
        if token_usage is not None:
            conv.token_usage = token_usage
        if dify_conversation_id and not conv.dify_conversation_id:
            conv.dify_conversation_id = dify_conversation_id
        conv.updated_at = datetime.now(timezone.utc)
        await session.commit()


async def stream_dify_events(
    dify: DifyClient,
    user: User,
    app_row: App,
    conversation: Conversation,
    query: str,
) -> AsyncIterator[str]:
    accumulated = ""
    token_usage: dict | None = None
    dify_conversation_id: str | None = None
    payload = {
        "inputs": {},
        "query": query,
        "response_mode": "streaming",
        "conversation_id": conversation.dify_conversation_id or "",
        "user": str(user.id),
    }

    terminal = False  # 正常结束 / 超时 / 错误 → 终端路径（区别于客户端断流）
    try:
        async with dify.stream_chat(app_row.id, payload) as resp:
            if resp.status_code != 200:
                yield _sse("error", {"message": f"Dify upstream error: {resp.status_code}"})
                terminal = True
            else:
                current_event = ""
                async for line in resp.aiter_lines():
                    if not line:
                        current_event = ""  # 事件块结束，防止下一块误用旧事件名
                        yield "\n"
                        continue
                    if line.startswith("event:"):
                        current_event = line.split(":", 1)[1].strip()
                        yield line + "\n"
                        continue
                    if line.startswith("data:"):
                        raw = line.split(":", 1)[1].strip()
                        try:
                            data = json.loads(raw)
                        except (json.JSONDecodeError, ValueError):
                            data = None
                        if isinstance(data, dict):
                            ev = current_event or str(data.get("event", ""))
                            if ev == "message":
                                accumulated += str(data.get("answer", ""))
                            elif ev == "message_end":
                                usage = (data.get("metadata") or {}).get("usage")
                                if isinstance(usage, dict):
                                    token_usage = usage
                            cid = data.get("conversation_id")
                            if cid:
                                dify_conversation_id = str(cid)
                    yield line + "\n"
                terminal = True
    except httpx.TimeoutException:
        # 流式不重试（设计 §7.3）：直接给前端错误事件
        yield _sse("error", {"message": "Dify timeout"})
        terminal = True
    except Exception:
        yield _sse("error", {"message": "Proxy error"})
        terminal = True
    finally:
        # 客户端断流（GeneratorExit）也会走到这里 —— 唯一的落库保证点
        await _persist_assistant(
            SessionLocal,
            conversation.id,
            accumulated,
            token_usage,
            dify_conversation_id,
        )

    if terminal:
        yield _sse("agent_done")

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

# 契约 v3：workflow 事件中需要丢弃的词汇（不透传给前端）
_WORKFLOW_DROP_EVENTS = {"ping", "workflow_started", "node_started", "node_finished"}


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
    files: list[dict] | None = None,
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
    # 契约 v4：附件经发送时转发，携带 Dify 形态的 files 参数
    if files:
        payload["files"] = files

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
        # 契约 v5：携带我方内部会话 UUID，前端据此认领（替代伪造 id/列表回查）
        yield _sse("agent_done", {"conversation_id": str(conversation.id)})


async def stream_workflow_events(
    dify: DifyClient,
    user: User,
    app_row: App,
    conversation: Conversation,
    inputs: dict,
) -> AsyncIterator[str]:
    """工作流模式（契约 v3）：调 /v1/workflows/run 并把事件翻译为统一对话词汇表。

    翻译映射：
    - ping / workflow_started / node_started / node_finished → 丢弃
    - text_chunk(data.text) → message(answer)
    - workflow_finished → message_end(usage.total=total_tokens)；
      status=failed → error(契约形状)，不吐 message_end
    - 非事件行 / 解析失败的行 → 跳过
    落库与 agent_done 语义与 chat 分支一致（finally 兜底 + 终端路径）。
    """
    accumulated = ""
    token_usage: dict | None = None
    payload = {
        "inputs": inputs,
        "response_mode": "streaming",
        "user": str(user.id),
    }

    terminal = False
    try:
        async with dify.stream_workflow(app_row.id, payload) as resp:
            if resp.status_code != 200:
                yield _sse("error", {"message": f"Dify upstream error: {resp.status_code}"})
                terminal = True
            else:
                # event 名优先取 event: 行（SSE 规范），回退 data.event（真实样本两者皆有）
                current_event = ""
                async for line in resp.aiter_lines():
                    if not line:
                        current_event = ""  # 事件块结束，防止误用上一块事件名
                        continue
                    if line.startswith("event:"):
                        current_event = line.split(":", 1)[1].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    raw = line.split(":", 1)[1].strip()
                    try:
                        data = json.loads(raw)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if not isinstance(data, dict):
                        continue
                    ev = current_event or str(data.get("event", ""))
                    if ev in _WORKFLOW_DROP_EVENTS:
                        continue
                    if ev == "text_chunk":
                        text = str((data.get("data") or {}).get("text") or "")
                        if text:
                            accumulated += text
                            yield _sse("message", {"answer": text})
                    elif ev == "workflow_finished":
                        finish = data.get("data") or {}
                        if finish.get("status") == "failed":
                            yield _sse(
                                "error",
                                {"message": str(finish.get("error") or "workflow failed")},
                            )
                        else:
                            total = int(finish.get("total_tokens") or 0)
                            token_usage = {"total": total}
                            yield _sse(
                                "message_end", {"metadata": {"usage": {"total": total}}}
                            )
                terminal = True
    except httpx.TimeoutException:
        yield _sse("error", {"message": "Dify timeout"})
        terminal = True
    except Exception:
        yield _sse("error", {"message": "Proxy error"})
        terminal = True
    finally:
        # 工作流无 Dify 会话概念：dify_conversation_id 恒 None
        await _persist_assistant(
            SessionLocal,
            conversation.id,
            accumulated,
            token_usage,
            None,
        )

    if terminal:
        # 契约 v5：携带我方内部会话 UUID，前端据此认领（替代伪造 id/列表回查）
        yield _sse("agent_done", {"conversation_id": str(conversation.id)})

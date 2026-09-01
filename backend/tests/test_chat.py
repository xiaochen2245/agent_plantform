"""POST /api/chat/send：SSE 透传、对话镜像、错误与断流路径。"""
import json

from httpx import AsyncClient
from sqlalchemy import select

from app.db.session import SessionLocal
from app.dify.deps import get_dify
from app.main import app
from app.models.conversation import Conversation
from app.models.message import Message
from tests.conftest import login
from tests.fake_dify import SSE_OK, exploding_dify_client, fake_dify_client, sse_events


def _use(dify) -> None:
    app.dependency_overrides[get_dify] = lambda: dify


async def _send(c: AsyncClient, query="帮我重启测试服务器", conversation_id=""):
    async with c.stream(
        "POST",
        "/api/chat/send",
        json={"app_id": 1, "query": query, "conversation_id": conversation_id},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert resp.headers["x-accel-buffering"] == "no"
        lines = [line async for line in resp.aiter_lines()]
    return lines


def _events(lines: list[str]) -> list[tuple[str, dict]]:
    """把转发出的行重组回 (event, data) 对，便于断言。"""
    out, ev = [], ""
    for line in lines:
        if line.startswith("event:"):
            ev = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            out.append((ev, json.loads(line.split(":", 1)[1].strip())))
    return out


async def _db_convs() -> list[Conversation]:
    async with SessionLocal() as s:
        return list((await s.execute(select(Conversation))).scalars())


async def _db_messages(conv_id) -> list[Message]:
    async with SessionLocal() as s:
        rows = (await s.execute(select(Message).where(Message.conversation_id == conv_id).order_by(Message.id))).scalars()
        return list(rows)


async def test_send_requires_auth(client: AsyncClient):
    resp = await client.post("/api/chat/send", json={"app_id": 1, "query": "hi"})
    assert resp.status_code == 401


async def test_send_unknown_app_404(client: AsyncClient):
    await login(client)
    resp = await client.post("/api/chat/send", json={"app_id": 99, "query": "hi"})
    assert resp.status_code == 404


async def test_send_creates_conversation_and_mirrors_both_messages(client: AsyncClient):
    _use(fake_dify_client(SSE_OK))
    await login(client)
    lines = await _send(client)

    # 透传：message×3 原样到达 + agent_done 收尾
    events = _events(lines)
    assert [e for e, _ in events[:3]] == ["message", "message", "message"]
    assert events[0][1] == {"answer": "你"}
    assert ("agent_done", {}) == events[-1]

    convs = await _db_convs()
    assert len(convs) == 1
    conv = convs[0]
    assert conv.user_id == 1 and conv.app_id == 1
    assert conv.title == "帮我重启测试服务器"  # query 前 20 字
    assert conv.message_count == 2  # user + assistant

    msgs = await _db_messages(conv.id)
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[0].content == "帮我重启测试服务器"
    assert msgs[1].content == "你好！"  # 增量累加结果


async def test_send_persists_usage_and_dify_conversation_id(client: AsyncClient):
    _use(fake_dify_client(SSE_OK))
    await login(client)
    await _send(client)

    conv = (await _db_convs())[0]
    assert conv.token_usage == {"prompt": 10, "completion": 20, "total": 30}
    assert conv.dify_conversation_id == "dify-conv-abc"


async def test_send_reuses_conversation_and_forwards_dify_id(client: AsyncClient):
    captured: list[dict] = []
    _use(fake_dify_client(SSE_OK, captured=captured))
    await login(client)

    await _send(client, query="第一问")  # 新建，流中回填 dify-conv-abc
    conv = (await _db_convs())[0]
    assert captured[0]["json"]["conversation_id"] == ""  # 首问不带 dify id

    await _send(client, query="第二问", conversation_id=str(conv.id))
    convs = await _db_convs()
    assert len(convs) == 1  # 复用，未新建
    assert captured[1]["json"]["conversation_id"] == "dify-conv-abc"  # 透传 Dify 侧会话
    assert captured[1]["json"]["user"] == "1"
    msgs = await _db_messages(conv.id)
    assert [m.role for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert convs[0].message_count == 4


async def test_send_unknown_conversation_404(client: AsyncClient):
    _use(fake_dify_client(SSE_OK))
    await login(client)
    resp = await client.post(
        "/api/chat/send",
        json={"app_id": 1, "query": "hi", "conversation_id": "not-a-uuid"},
    )
    assert resp.status_code == 404


async def test_send_dify_500_yields_error_event(client: AsyncClient):
    _use(fake_dify_client(b"upstream exploded", status=500))
    await login(client)
    lines = await _send(client)

    events = _events(lines)
    assert events[0][0] == "error"
    assert "500" in events[0][1]["message"]
    assert events[-1] == ("agent_done", {})

    # 用户消息已落，assistant 无内容不落
    conv = (await _db_convs())[0]
    msgs = await _db_messages(conv.id)
    assert [m.role for m in msgs] == ["user"]
    assert conv.message_count == 1


async def test_send_midstream_abort_persists_partial_content(client: AsyncClient):
    first = sse_events(("message", {"answer": "部分回"}))
    _use(exploding_dify_client(first))
    await login(client)
    lines = await _send(client)

    # 前段透传到达，随后错误事件
    events = _events(lines)
    assert events[0] == ("message", {"answer": "部分回"})
    assert any(e == "error" for e, _ in events)

    # 断流仍落已累加内容（finally 块保证）
    conv = (await _db_convs())[0]
    msgs = await _db_messages(conv.id)
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[1].content == "部分回"

"""契约 v6：思考过程透传（reasoning 事件）、翻译与持久化。"""
import json

from httpx import AsyncClient

from app.dify.deps import get_dify
from app.main import app
from tests.conftest import login
from tests.fake_dify import SSE_OK, fake_dify_client, sse_events


def _use(dify) -> None:
    app.dependency_overrides[get_dify] = lambda: dify


async def _send(c: AsyncClient, query="深思一下"):
    async with c.stream(
        "POST", "/api/chat/send", json={"app_id": 1, "query": query, "conversation_id": ""}
    ) as resp:
        assert resp.status_code == 200
        return [line async for line in resp.aiter_lines()]


def _events(lines: list[str]) -> list[tuple[str, dict]]:
    out, ev = [], ""
    for line in lines:
        if line.startswith("event:"):
            ev = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            out.append((ev, json.loads(line.split(":", 1)[1].strip())))
    return out


async def test_message_frame_with_reasoning_emits_reasoning_before_answer(client: AsyncClient):
    """a) message 同帧带 reasoning_content → 先出 reasoning 事件再透传原行。"""
    _use(fake_dify_client(sse_events(
        ("message", {"answer": "先想", "reasoning_content": "用户在问数学"}),
        ("message", {"answer": "再答"}),
        ("message_end", {"metadata": {"usage": {"total": 5}}}),
    )))
    await login(client)
    events = _events(await _send(client))
    reasoning = [(e, d) for e, d in events if e == "reasoning"]
    assert reasoning == [("reasoning", {"content": "用户在问数学"})]
    # 顺序：reasoning 在其对应 answer 帧之前
    names = [e for e, _ in events]
    assert names.index("reasoning") < names.index("message")
    # answer 透传不受影响
    answers = [d["answer"] for e, d in events if e == "message"]
    assert answers == ["先想", "再答"]


async def test_agent_thought_event_translated_and_original_suppressed(client: AsyncClient):
    """b) agent_thought 独立事件 → 翻译为 reasoning，不透传原事件名。"""
    _use(fake_dify_client(sse_events(
        ("agent_thought", {"thought": "第一步：查知识库"}),
        ("message", {"answer": "结论"}),
        ("message_end", {"metadata": {"usage": {"total": 3}}}),
    )))
    await login(client)
    lines = await _send(client)
    events = _events(lines)
    assert ("reasoning", {"content": "第一步：查知识库"}) in events
    # 原始 agent_thought 不出现在转发流里（翻译而非透传）
    assert not any("agent_thought" in line for line in lines)


async def test_plain_stream_has_no_reasoning_events(client: AsyncClient):
    """c) 无思考的普通流完全不变（回归保护）。"""
    _use(fake_dify_client(SSE_OK))
    await login(client)
    events = _events(await _send(client))
    assert [e for e, _ in events if e == "reasoning"] == []
    assert [e for e, _ in events].count("message") == 3


async def test_reasoning_persisted_and_returned_in_detail(client: AsyncClient):
    """d) 思考随消息落库，messages 详情输出 reasoning 字段。"""
    _use(fake_dify_client(sse_events(
        ("message", {"answer": "答", "reasoning_content": "思考A"}),
        ("message_end", {"metadata": {"usage": {"total": 2}}}),
    )))
    await login(client)
    lines = await _send(client)
    conv_id = None
    for e, d in _events(lines):
        if e == "agent_done":
            conv_id = d["conversation_id"]
    assert conv_id

    resp = await client.get(f"/api/conversations/{conv_id}/messages")
    assert resp.status_code == 200
    messages = resp.json()["messages"]
    assistant = [m for m in messages if m["role"] == "assistant"][0]
    assert assistant["reasoning"] == "思考A"
    # user 消息与无思考消息的 reasoning 为 null（契约 v6 缺省）
    user_msg = [m for m in messages if m["role"] == "user"][0]
    assert user_msg["reasoning"] is None

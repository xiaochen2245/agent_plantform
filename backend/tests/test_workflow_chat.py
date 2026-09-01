"""工作流模式（契约 v3）：事件翻译、inputs 校验/透传、apps/me schema、落库。"""
import json

from httpx import AsyncClient
from sqlalchemy import select

from app.db.session import SessionLocal
from app.dify.deps import get_dify
from app.main import app
from app.models.app import App
from app.models.conversation import Conversation
from app.models.message import Message
from tests.conftest import login
from tests.fake_dify import (
    WORKFLOW_SSE_FAILED,
    WORKFLOW_SSE_OK,
    fake_dify_client,
)
from tests.test_chat import _db_convs, _db_messages, _events


def _use(dify) -> None:
    app.dependency_overrides[get_dify] = lambda: dify


async def _send_workflow(c: AsyncClient, inputs: dict, query="张三 产品经理 13800000000"):
    async with c.stream(
        "POST",
        "/api/chat/send",
        json={"app_id": 4, "query": query, "conversation_id": "", "inputs": inputs},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines()]
    return lines


async def test_workflow_translates_events_to_chat_vocabulary(client: AsyncClient):
    captured: list = []
    _use(fake_dify_client(WORKFLOW_SSE_OK, captured=captured))
    await login(client)

    lines = await _send_workflow(client, {"business_card": "张三 产品经理 13800000000"})
    events = _events(lines)
    names = [name for name, _ in events]

    # 丢弃 ping / workflow_started / node_*；text_chunk→message；workflow_finished→message_end
    assert names == ["message", "message", "message_end", "agent_done"]
    assert events[0][1]["answer"] == "名"
    assert events[1][1]["answer"] == "片已生成：张三 产品经理"
    assert events[2][1]["metadata"]["usage"]["total"] == 77


async def test_workflow_inputs_passed_to_upstream(client: AsyncClient):
    captured: list = []
    _use(fake_dify_client(WORKFLOW_SSE_OK, captured=captured))
    await login(client)

    await _send_workflow(client, {"business_card": "李四 设计师"})

    assert len(captured) == 1
    req = captured[0]
    assert req["url"].endswith("/v1/workflows/run")
    assert req["json"]["inputs"] == {"business_card": "李四 设计师"}
    assert req["json"]["response_mode"] == "streaming"


async def test_workflow_missing_required_input_400(client: AsyncClient):
    _use(fake_dify_client(WORKFLOW_SSE_OK))
    await login(client)

    resp = await client.post(
        "/api/chat/send",
        json={"app_id": 4, "query": "x", "conversation_id": "", "inputs": {}},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "missing required input: business_card"


async def test_workflow_failed_run_emits_error_not_message_end(client: AsyncClient):
    _use(fake_dify_client(WORKFLOW_SSE_FAILED))
    await login(client)

    lines = await _send_workflow(client, {"business_card": "王五"})
    events = _events(lines)
    names = [name for name, _ in events]

    assert names == ["error", "agent_done"]
    assert "business_card is required" in events[0][1]["message"]


async def test_workflow_persists_messages_and_usage(client: AsyncClient):
    _use(fake_dify_client(WORKFLOW_SSE_OK))
    await login(client)

    await _send_workflow(client, {"business_card": "张三 产品经理 13800000000"})

    convs = await _db_convs()
    assert len(convs) == 1
    conv = convs[0]
    # 契约 v3：title 取首个 inputs 值；无 dify_conversation_id
    assert conv.title == "张三 产品经理 13800000000"[:20]
    assert conv.dify_conversation_id is None
    assert conv.token_usage == {"total": 77}

    msgs = await _db_messages(conv.id)
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[1].content == "名片已生成：张三 产品经理"


async def test_workflow_failed_run_still_persists_user_message(client: AsyncClient):
    _use(fake_dify_client(WORKFLOW_SSE_FAILED))
    await login(client)

    await _send_workflow(client, {"business_card": "王五"}, query="王五 前端工程师")

    convs = await _db_convs()
    msgs = await _db_messages(convs[0].id)
    # 失败流无 text_chunk → 不落 assistant；user 消息仍保留（契约：开流前落库）
    assert [m.role for m in msgs] == ["user"]
    assert convs[0].token_usage is None


async def test_apps_me_includes_workflow_app_with_schema(client: AsyncClient):
    await login(client)

    resp = await client.get("/api/apps/me")
    assert resp.status_code == 200
    apps = resp.json()["apps"]
    by_id = {a["id"]: a for a in apps}
    assert 4 in by_id, f"seed 应含工作流应用，实际: {[a['id'] for a in apps]}"
    wf = by_id[4]
    assert wf["mode"] == "workflow"
    schema = wf["inputs_schema"]
    assert schema[0]["name"] == "business_card"
    assert schema[0]["required"] is True
    # chat 应用 schema 为 null（契约：仅 workflow 非空）
    assert by_id[1]["inputs_schema"] is None


async def test_seed_app4_idempotent(client: AsyncClient):
    """存量库二次 init_db 只补新增行，不重复插 app 4。"""
    from app.db.init import init_db

    await init_db()  # fresh_db 已建过一次；再跑一遍验证幂等
    async with SessionLocal() as s:
        apps = list((await s.execute(select(App))).scalars())
    assert len([a for a in apps if a.dify_app_id == "app-test-004"]) == 1
    assert len(apps) == 4

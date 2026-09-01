"""GET /api/conversations：本人隔离 / app_id 过滤 / 软删排除 / 排序。"""
import uuid
from datetime import datetime, timezone

from httpx import AsyncClient
from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.conversation import Conversation
from app.models.user import User
from tests.conftest import login


async def _make_user(email: str) -> int:
    async with SessionLocal() as s:
        u = User(email=email, name=email.split("@")[0], password_hash=hash_password("x123456"))
        s.add(u)
        await s.commit()
        return u.id


async def _make_conv(user_id: int, app_id: int, title: str, deleted=False) -> uuid.UUID:
    async with SessionLocal() as s:
        c = Conversation(
            user_id=user_id, app_id=app_id, title=title, message_count=2,
            deleted_at=datetime.now(timezone.utc) if deleted else None,
        )
        s.add(c)
        await s.commit()
        return c.id


async def test_requires_auth(client: AsyncClient):
    resp = await client.get("/api/conversations")
    assert resp.status_code == 401


async def test_only_own_conversations_visible(client: AsyncClient):
    await login(client)  # 种子 admin（user 1）
    mine = await _make_conv(user_id=1, app_id=1, title="我的会话")
    other = await _make_user("bob@company.com")
    await _make_conv(user_id=other, app_id=1, title="Bob 的会话")

    resp = await client.get("/api/conversations")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [i["id"] for i in items] == [str(mine)]
    item = items[0]
    assert item["title"] == "我的会话"
    assert item["message_count"] == 2
    assert "T" in item["updated_at"]  # ISO8601


async def test_app_id_filter_and_soft_delete_excluded(client: AsyncClient):
    await login(client)
    kept = await _make_conv(user_id=1, app_id=2, title="报销相关")
    await _make_conv(user_id=1, app_id=1, title="运维相关")
    await _make_conv(user_id=1, app_id=2, title="已删除的报销会话", deleted=True)

    resp = await client.get("/api/conversations", params={"app_id": 2})
    items = resp.json()["items"]
    assert [i["id"] for i in items] == [str(kept)]


# ── GET /api/conversations/{id}/messages（契约 v2）──────────────────────
from app.models.message import Message  # noqa: E402


async def _make_messages(conv_id: uuid.UUID, contents: list[tuple[str, str]]) -> None:
    async with SessionLocal() as s:
        for role, content in contents:
            s.add(Message(conversation_id=conv_id, role=role, content=content))
        await s.commit()


async def test_messages_detail_shape_and_order(client: AsyncClient):
    await login(client)  # admin
    conv = await _make_conv(user_id=1, app_id=1, title="详情会话")
    await _make_messages(conv, [("user", "第一问"), ("assistant", "第一答"), ("user", "第二问")])

    resp = await client.get(f"/api/conversations/{conv}/messages")
    assert resp.status_code == 200
    msgs = resp.json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert [m["content"] for m in msgs] == ["第一问", "第一答", "第二问"]
    assert all(isinstance(m["id"], int) for m in msgs)
    assert all("T" in m["created_at"] for m in msgs)  # ISO8601


async def test_messages_detail_isolation(client: AsyncClient):
    await login(client)  # admin
    conv = await _make_conv(user_id=1, app_id=1, title="admin 私有")
    await _make_messages(conv, [("user", "hi")])

    other = await _make_user("eve@company.com")
    from app.models.role import Role, user_roles  # noqa: E402

    async with SessionLocal() as s:
        user_role = await s.scalar(select(Role).where(Role.code == "USER"))
        await s.execute(
            user_roles.insert().values(user_id=other, role_id=user_role.id)
        )
        await s.commit()
    await login(client, "eve@company.com", "x123456")

    resp = await client.get(f"/api/conversations/{conv}/messages")
    assert resp.status_code == 404  # 他人会话不可见


async def test_messages_detail_404_cases(client: AsyncClient):
    await login(client)
    deleted = await _make_conv(user_id=1, app_id=1, title="已删", deleted=True)
    await _make_messages(deleted, [("user", "hi")])

    assert (await client.get(f"/api/conversations/{deleted}/messages")).status_code == 404
    assert (
        (await client.get("/api/conversations/not-a-uuid/messages")).status_code == 404
    )
    assert (
        (await client.get(f"/api/conversations/{uuid.uuid4()}/messages")).status_code
        == 404
    )

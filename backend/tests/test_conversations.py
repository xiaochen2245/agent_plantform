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

"""三态授权并集语义（设计 §4.3）：apps/me 过滤 + chat/send 403 前置。

GUEST/FIN 为测试专用角色（无种子授权），用来精确控制并集的每一路。
"""
from httpx import AsyncClient
from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.dify.deps import get_dify
from app.main import app
from app.models import AppAuthorization, Department, Role, User, user_roles
from tests.conftest import login
from tests.fake_dify import SSE_OK, fake_dify_client


async def mkuser(
    email: str, role_codes: tuple[str, ...] = ("GUEST",), dept_id: int | None = None
) -> int:
    """直插用户+user_roles（绕过 admin API，专注授权语义本身）。"""
    async with SessionLocal() as s:
        for code in role_codes:
            if await s.scalar(select(Role).where(Role.code == code)) is None:
                s.add(Role(code=code, name=code))
        await s.flush()
        user = User(
            email=email,
            name=email.split("@")[0],
            password_hash=hash_password("guest-pass-123"),
            roles=list(role_codes),  # JSON 历史快照
            dept_id=dept_id,
        )
        s.add(user)
        await s.flush()
        for code in role_codes:
            role = await s.scalar(select(Role).where(Role.code == code))
            await s.execute(
                user_roles.insert().values(user_id=user.id, role_id=role.id)
            )
        await s.commit()
        return user.id


async def grant(app_id: int, principal_type: str, principal_id: int) -> None:
    async with SessionLocal() as s:
        s.add(
            AppAuthorization(
                app_id=app_id, principal_type=principal_type, principal_id=principal_id
            )
        )
        await s.commit()


async def _role_id(code: str) -> int:
    async with SessionLocal() as s:
        return (await s.scalar(select(Role).where(Role.code == code))).id


async def _apps_of(client: AsyncClient) -> list[int]:
    resp = await client.get("/api/apps/me")
    assert resp.status_code == 200
    return [a["id"] for a in resp.json()["apps"]]


async def test_user_role_sees_all_seed_apps(client: AsyncClient):
    """USER 角色经种子 role 授权全员可见（保住既有体验）。"""
    await mkuser("u1@company.com", role_codes=("USER",))
    await login(client, "u1@company.com", "guest-pass-123")
    assert await _apps_of(client) == [1, 2, 3]


async def test_direct_grant_only(client: AsyncClient):
    uid = await mkuser("u2@company.com")
    await grant(3, "user", uid)
    await login(client, "u2@company.com", "guest-pass-123")
    assert await _apps_of(client) == [3]


async def test_dept_grant(client: AsyncClient):
    async with SessionLocal() as s:
        s.add(Department(id=5, name="财务部", parent_id=None, path="/5/"))
        await s.commit()
    uid = await mkuser("u3@company.com", dept_id=5)
    await grant(2, "dept", 5)
    await login(client, "u3@company.com", "guest-pass-123")
    assert await _apps_of(client) == [2]


async def test_role_grant(client: AsyncClient):
    await mkuser("u4@company.com", role_codes=("GUEST", "FIN"))
    fin_id = await _role_id("FIN")
    await grant(1, "role", fin_id)
    await login(client, "u4@company.com", "guest-pass-123")
    assert await _apps_of(client) == [1]


async def test_union_of_three_principals(client: AsyncClient):
    async with SessionLocal() as s:
        s.add(Department(id=7, name="研发部", parent_id=None, path="/7/"))
        await s.commit()
    uid = await mkuser("u5@company.com", role_codes=("GUEST", "FIN"), dept_id=7)
    fin_id = await _role_id("FIN")
    await grant(3, "user", uid)   # 直授 → 3
    await grant(2, "dept", 7)     # 部门 → 2
    await grant(1, "role", fin_id)  # 角色 → 1
    await login(client, "u5@company.com", "guest-pass-123")
    assert await _apps_of(client) == [1, 2, 3]


async def test_no_grant_invisible_and_chat_403(client: AsyncClient):
    await mkuser("u6@company.com")  # GUEST，零授权
    await login(client, "u6@company.com", "guest-pass-123")
    assert await _apps_of(client) == []

    resp = await client.post(
        "/api/chat/send", json={"app_id": 1, "query": "hi", "conversation_id": ""}
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Not authorized for this app"


async def test_platform_admin_unrestricted(client: AsyncClient):
    """PLATFORM_ADMIN 即使零个人授权也全可见。"""
    await mkuser("boss@company.com", role_codes=("PLATFORM_ADMIN",))
    await login(client, "boss@company.com", "guest-pass-123")
    assert await _apps_of(client) == [1, 2, 3]


async def test_authorized_user_can_still_chat(client: AsyncClient):
    """直授用户照常走 SSE（授权闸不误伤正常路径）。"""
    app.dependency_overrides[get_dify] = lambda: fake_dify_client(SSE_OK)
    uid = await mkuser("u7@company.com")
    await grant(1, "user", uid)
    await login(client, "u7@company.com", "guest-pass-123")
    async with client.stream(
        "POST",
        "/api/chat/send",
        json={"app_id": 1, "query": "在吗", "conversation_id": ""},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines()]
    assert any(line.startswith("data:") for line in lines)

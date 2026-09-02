"""角色管理：CRUD + 内置保护 + 删除时清理 user_roles / app_authorizations。"""
from httpx import AsyncClient
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import AppAuthorization, Role, user_roles
from tests.conftest import login
from tests.test_authz import grant, mkuser


async def test_non_admin_gets_403(client: AsyncClient):
    await mkuser("g@company.com")
    await login(client, "g@company.com", "guest-pass-123")
    for method, url in [
        ("get", "/api/admin/roles"),
        ("post", "/api/admin/roles"),
        ("patch", "/api/admin/roles/1"),
        ("delete", "/api/admin/roles/1"),
    ]:
        resp = await client.request(method, url, json={"code": "X", "name": "X"})
        assert resp.status_code == 403, (method, url, resp.text)


async def test_list_seeded_roles(client: AsyncClient):
    await login(client)
    resp = await client.get("/api/admin/roles")
    items = resp.json()["items"]
    codes = {r["code"] for r in items}
    assert codes == {"USER", "PLATFORM_ADMIN"}


async def test_create_with_code_pattern(client: AsyncClient):
    await login(client)
    resp = await client.post(
        "/api/admin/roles", json={"code": "FINANCE_ADMIN", "name": "财务管理员"}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["code"] == "FINANCE_ADMIN"
    assert body["name"] == "财务管理员"


async def test_create_lowercase_normalized(client: AsyncClient):
    await login(client)
    resp = await client.post(
        "/api/admin/roles", json={"code": "finance_admin", "name": "x"}
    )
    assert resp.status_code == 201
    assert resp.json()["code"] == "FINANCE_ADMIN"


async def test_create_invalid_code_422(client: AsyncClient):
    await login(client)
    resp = await client.post(
        "/api/admin/roles", json={"code": "1starts-with-digit", "name": "x"}
    )
    assert resp.status_code == 422


async def test_create_duplicate_409(client: AsyncClient):
    await login(client)
    await client.post("/api/admin/roles", json={"code": "DUP", "name": "x"})
    resp = await client.post("/api/admin/roles", json={"code": "DUP", "name": "y"})
    assert resp.status_code == 409


async def test_patch_rename(client: AsyncClient):
    await login(client)
    r = await client.post("/api/admin/roles", json={"code": "HR", "name": "原名"})
    rid = r.json()["id"]
    resp = await client.patch(f"/api/admin/roles/{rid}", json={"name": "新名"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "新名"


async def test_patch_unknown_404(client: AsyncClient):
    await login(client)
    resp = await client.patch("/api/admin/roles/9999", json={"name": "x"})
    assert resp.status_code == 404


async def test_delete_custom_role_cleans_relations(client: AsyncClient):
    """删除自定义角色：清理 user_roles 和 role 类型 app_authorizations。"""
    await login(client)
    rid_resp = await client.post(
        "/api/admin/roles", json={"code": "TO_DEL", "name": "to-del"}
    )
    rid = rid_resp.json()["id"]

    # 创建一个拥有该角色的用户
    await mkuser("role-user@company.com", role_codes=("USER", "TO_DEL"))
    # 给该角色一个 app 授权
    await client.put(f"/api/admin/roles/{rid}/apps", json={"app_ids": [1]})

    resp = await client.delete(f"/api/admin/roles/{rid}")
    assert resp.status_code == 204

    # 验证 user_roles 行已清
    async with SessionLocal() as s:
        rows = (await s.execute(select(user_roles).where(user_roles.c.role_id == rid))).all()
        assert rows == []
        auth_rows = (
            await s.execute(
                select(AppAuthorization).where(
                    AppAuthorization.principal_type == "role",
                    AppAuthorization.principal_id == rid,
                )
            )
        ).scalars().all()
        assert auth_rows == []


async def test_delete_builtin_role_400(client: AsyncClient):
    await login(client)
    # USER
    async with SessionLocal() as s:
        user_role = (await s.execute(select(Role).where(Role.code == "USER"))).scalar_one()
    resp = await client.delete(f"/api/admin/roles/{user_role.id}")
    assert resp.status_code == 400
    assert "builtin" in resp.json()["detail"]

    # PLATFORM_ADMIN
    async with SessionLocal() as s:
        admin_role = (
            await s.execute(select(Role).where(Role.code == "PLATFORM_ADMIN"))
        ).scalar_one()
    resp = await client.delete(f"/api/admin/roles/{admin_role.id}")
    assert resp.status_code == 400


async def test_delete_unknown_404(client: AsyncClient):
    await login(client)
    resp = await client.delete("/api/admin/roles/9999")
    assert resp.status_code == 404

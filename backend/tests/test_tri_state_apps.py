"""三态授权管理端点：dept / role（user 已有 test_admin.py 覆盖）。"""
from httpx import AsyncClient
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import Department, Role
from tests.conftest import login
from tests.test_authz import mkuser


async def _dept(client: AsyncClient, name: str = "研发") -> dict:
    resp = await client.post("/api/admin/depts", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _role(client: AsyncClient, code: str = "FIN", name: str = "财务") -> dict:
    resp = await client.post("/api/admin/roles", json={"code": code, "name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_dept_apps_roundtrip_and_visibility(client: AsyncClient):
    """部门级授权全量替换 + 影响该部门下所有用户的可见性。"""
    await login(client)
    d = await _dept(client)
    await client.put(
        f"/api/admin/depts/{d['id']}/apps", json={"app_ids": [2]}
    )  # 预置 2

    resp = await client.get(f"/api/admin/depts/{d['id']}/apps")
    assert resp.json() == {"app_ids": [2]}

    resp = await client.put(
        f"/api/admin/depts/{d['id']}/apps", json={"app_ids": [1, 3]}
    )
    assert resp.status_code == 200
    assert resp.json() == {"app_ids": [1, 3]}

    resp = await client.get(f"/api/admin/depts/{d['id']}/apps")
    assert resp.json() == {"app_ids": [1, 3]}  # 全量替换：2 被移除

    # 该部门下的用户看到 [1, 3]
    await mkuser("dept-user@x.com", dept_id=d["id"])
    await login(client, "dept-user@x.com", "guest-pass-123")
    resp = await client.get("/api/apps/me")
    assert [a["id"] for a in resp.json()["apps"]] == [1, 3]


async def test_dept_apps_unknown_dept_404(client: AsyncClient):
    await login(client)
    resp = await client.get("/api/admin/depts/9999/apps")
    assert resp.status_code == 404
    resp = await client.put("/api/admin/depts/9999/apps", json={"app_ids": [1]})
    assert resp.status_code == 404


async def test_dept_apps_unknown_app_400(client: AsyncClient):
    await login(client)
    d = await _dept(client)
    resp = await client.put(
        f"/api/admin/depts/{d['id']}/apps", json={"app_ids": [9999]}
    )
    assert resp.status_code == 400


async def test_dept_apps_non_admin_403(client: AsyncClient):
    await login(client)
    d = await _dept(client)
    await mkuser("g@x.com")
    await login(client, "g@x.com", "guest-pass-123")
    resp = await client.get(f"/api/admin/depts/{d['id']}/apps")
    assert resp.status_code == 403
    resp = await client.put(
        f"/api/admin/depts/{d['id']}/apps", json={"app_ids": [1]}
    )
    assert resp.status_code == 403


async def test_role_apps_roundtrip_and_visibility(client: AsyncClient):
    await login(client)
    r = await _role(client)
    await client.put(f"/api/admin/roles/{r['id']}/apps", json={"app_ids": [1]})

    resp = await client.get(f"/api/admin/roles/{r['id']}/apps")
    assert resp.json() == {"app_ids": [1]}

    resp = await client.put(
        f"/api/admin/roles/{r['id']}/apps", json={"app_ids": [2, 4]}
    )
    assert resp.status_code == 200
    assert resp.json() == {"app_ids": [2, 4]}

    # 拥有该角色的用户可见 [2, 4]
    await mkuser("role-user@x.com", role_codes=("FIN",))
    await login(client, "role-user@x.com", "guest-pass-123")
    resp = await client.get("/api/apps/me")
    assert [a["id"] for a in resp.json()["apps"]] == [2, 4]


async def test_role_apps_unknown_role_404(client: AsyncClient):
    await login(client)
    resp = await client.get("/api/admin/roles/9999/apps")
    assert resp.status_code == 404
    resp = await client.put("/api/admin/roles/9999/apps", json={"app_ids": [1]})
    assert resp.status_code == 404


async def test_role_apps_unknown_app_400(client: AsyncClient):
    await login(client)
    r = await _role(client)
    resp = await client.put(
        f"/api/admin/roles/{r['id']}/apps", json={"app_ids": [9999]}
    )
    assert resp.status_code == 400


async def test_union_of_dept_role_grants(client: AsyncClient):
    """用户可见 = dept 授权 ∪ role 授权 ∪ user 直授（已有 test_authz 覆盖）。"""
    await login(client)
    d1 = await _dept(client, "A 部")
    d2 = await _dept(client, "B 部")
    r1 = await _role(client, "AUDIT", "审计")

    await client.put(f"/api/admin/depts/{d1['id']}/apps", json={"app_ids": [1]})
    await client.put(f"/api/admin/depts/{d2['id']}/apps", json={"app_ids": [2]})
    await client.put(f"/api/admin/roles/{r1['id']}/apps", json={"app_ids": [3]})

    # 用户在 d1 + 拥有 AUDIT → 应见 [1, 3]
    await mkuser("u1@x.com", role_codes=("AUDIT",), dept_id=d1["id"])
    await login(client, "u1@x.com", "guest-pass-123")
    resp = await client.get("/api/apps/me")
    assert [a["id"] for a in resp.json()["apps"]] == [1, 3]

    # 用户在 d2 + 拥有 AUDIT → 应见 [2, 3]
    await mkuser("u2@x.com", role_codes=("AUDIT",), dept_id=d2["id"])
    await login(client, "u2@x.com", "guest-pass-123")
    resp = await client.get("/api/apps/me")
    assert [a["id"] for a in resp.json()["apps"]] == [2, 3]


async def test_seed_role_grant_to_user_still_works(client: AsyncClient):
    """种子：USER 角色持有 app1-4 授权，全员可见（保住既有契约）。"""
    await login(client)
    async with SessionLocal() as s:
        user_role = (await s.execute(select(Role).where(Role.code == "USER"))).scalar_one()
    resp = await client.get(f"/api/admin/roles/{user_role.id}/apps")
    assert resp.json() == {"app_ids": [1, 2, 3, 4]}

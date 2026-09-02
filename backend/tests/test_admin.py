"""Admin 用户管理端点（契约 v2 §Admin）：权限门 / CRUD / 重置密码 / 用户级授权。"""
from httpx import AsyncClient

from tests.conftest import login
from tests.test_authz import grant, mkuser

NEW_USER = {"name": "李雷", "email": "lei@company.com", "password": "lei-pass-123"}


async def _create(client: AsyncClient, **overrides) -> dict:
    payload = {**NEW_USER, **overrides}
    resp = await client.post("/api/admin/users", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_non_admin_gets_403(client: AsyncClient):
    await mkuser("guest1@company.com")
    await login(client, "guest1@company.com", "guest-pass-123")
    resp = await client.get("/api/admin/users")
    assert resp.status_code == 403
    # 管理面所有写端点同样拦截
    resp = await client.post(
        "/api/admin/users", json={**NEW_USER, "email": "x@company.com"}
    )
    assert resp.status_code == 403


async def test_create_user_defaults_and_login(client: AsyncClient):
    await login(client)  # admin
    created = await _create(client)
    assert created["roles"] == ["USER"]  # 契约：缺省 USER
    assert created["status"] == 1
    assert created["dept"] is None

    await login(client, NEW_USER["email"], NEW_USER["password"])
    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["roles"] == ["USER"]


async def test_create_duplicate_email_409(client: AsyncClient):
    await login(client)
    await _create(client)
    resp = await client.post("/api/admin/users", json=NEW_USER)
    assert resp.status_code == 409


async def test_list_query_and_pagination(client: AsyncClient):
    await login(client)
    await _create(client, name="张一", email="z1@company.com")
    await _create(client, name="张二", email="z2@company.com")
    await _create(client, name="李三", email="l3@company.com")

    resp = await client.get("/api/admin/users", params={"query": "张"})
    body = resp.json()
    assert body["total"] == 2
    assert {i["name"] for i in body["items"]} == {"张一", "张二"}

    resp = await client.get("/api/admin/users", params={"query": "company.com", "page_size": 2})
    body = resp.json()
    assert body["total"] == 4  # admin + 3
    assert len(body["items"]) == 2

    resp = await client.get("/api/admin/users", params={"status": 1, "page_size": 100})
    assert resp.json()["total"] == 4


async def test_patch_roles_name_status(client: AsyncClient):
    await login(client)
    created = await _create(client)

    resp = await client.patch(
        f"/api/admin/users/{created['id']}", json={"roles": ["USER", "PLATFORM_ADMIN"]}
    )
    assert resp.status_code == 200
    assert sorted(resp.json()["roles"]) == ["PLATFORM_ADMIN", "USER"]

    # 状态禁用后该用户登录被拒
    resp = await client.patch(
        f"/api/admin/users/{created['id']}", json={"status": 0}
    )
    assert resp.status_code == 200 and resp.json()["status"] == 0
    resp = await client.post(
        "/api/auth/login",
        json={"email": NEW_USER["email"], "password": NEW_USER["password"]},
    )
    assert resp.status_code == 401


async def test_patch_self_disable_400(client: AsyncClient):
    await login(client)  # admin id=1
    resp = await client.patch("/api/admin/users/1", json={"status": 0})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Cannot disable yourself"


async def test_patch_unknown_user_404(client: AsyncClient):
    await login(client)
    resp = await client.patch("/api/admin/users/9999", json={"name": "无名"})
    assert resp.status_code == 404


async def test_patch_unknown_role_400(client: AsyncClient):
    await login(client)
    created = await _create(client)
    resp = await client.patch(
        f"/api/admin/users/{created['id']}", json={"roles": ["NO_SUCH_ROLE"]}
    )
    assert resp.status_code == 400
    assert "NO_SUCH_ROLE" in resp.json()["detail"]


async def test_reset_password_revokes_all_refresh_tokens(client: AsyncClient):
    await login(client)
    created = await _create(client)

    # 用户登录拿 refresh
    await login(client, NEW_USER["email"], NEW_USER["password"])
    old_refresh = client.cookies.get("refresh_token_cookie")

    await login(client)  # 切回 admin
    resp = await client.post(f"/api/admin/users/{created['id']}/reset_password")
    assert resp.status_code == 200
    new_password = resp.json()["password"]
    assert len(new_password) == 8

    # 旧 refresh 已被踢下线
    client.cookies.set("refresh_token_cookie", old_refresh)
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 401

    # 新密码可登录
    resp = await client.post(
        "/api/auth/login",
        json={"email": NEW_USER["email"], "password": new_password},
    )
    assert resp.status_code == 200


async def test_user_apps_roundtrip_and_visibility(client: AsyncClient):
    await login(client)
    uid = await mkuser("apps-user@company.com")  # GUEST：零角色授权
    await grant(2, "user", uid)  # 预置一条旧授权，验证全量替换语义

    resp = await client.get(f"/api/admin/users/{uid}/apps")
    assert resp.json() == {"app_ids": [2]}

    resp = await client.put(f"/api/admin/users/{uid}/apps", json={"app_ids": [1, 3]})
    assert resp.status_code == 200
    assert resp.json() == {"app_ids": [1, 3]}

    resp = await client.get(f"/api/admin/users/{uid}/apps")
    assert resp.json() == {"app_ids": [1, 3]}  # 全量替换：2 被移除

    # 用户侧可见性随之变化（授权闸真正生效）
    await login(client, "apps-user@company.com", "guest-pass-123")
    resp = await client.get("/api/apps/me")
    assert [a["id"] for a in resp.json()["apps"]] == [1, 3]


async def test_user_apps_unknown_app_400_and_unknown_user_404(client: AsyncClient):
    await login(client)
    resp = await client.put("/api/admin/users/1/apps", json={"app_ids": [99]})
    assert resp.status_code == 400
    resp = await client.put("/api/admin/users/9999/apps", json={"app_ids": [1]})
    assert resp.status_code == 404


async def test_csrf_guards_admin_writes(client: AsyncClient):
    """evil Origin 打管理端写接口 → 403（CSRF 中间件覆盖 /api/admin/*）。"""
    await login(client)
    resp = await client.post(
        "/api/admin/users",
        json={**NEW_USER, "email": "csrf@company.com"},
        headers={"Origin": "https://evil.com"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Forbidden: invalid origin"


async def test_user_out_includes_dept_id(client: AsyncClient):
    """契约：列表/创建/更新回显 dept_id（前端「设置部门」按 id 回显，名称可重名）。"""
    await login(client)
    await client.post("/api/admin/depts", json={"name": "dept-a"})
    created = await client.post(
        "/api/admin/users",
        json={"name": "u1", "email": "u1@company.com", "password": "pass-123", "dept_id": 1},
    )
    assert created.status_code == 201
    assert created.json()["dept_id"] == 1
    listed = await client.get("/api/admin/users", params={"query": "u1@"})
    assert listed.json()["items"][0]["dept_id"] == 1
    moved = await client.patch(
        f"/api/admin/users/{created.json()['id']}", json={"dept_id": None}
    )
    assert moved.json()["dept_id"] is None

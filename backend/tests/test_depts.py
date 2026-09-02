"""部门管理：CRUD + 物化路径维护 + 删除保护（契约 v2 §Admin 补齐）。"""
from httpx import AsyncClient
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import Department
from tests.conftest import login
from tests.test_authz import grant, mkuser


async def _create(client: AsyncClient, name: str, parent_id: int | None = None) -> dict:
    resp = await client.post("/api/admin/depts", json={"name": name, "parent_id": parent_id})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_non_admin_gets_403(client: AsyncClient):
    await mkuser("g@company.com")
    await login(client, "g@company.com", "guest-pass-123")
    for method, url in [
        ("get", "/api/admin/depts"),
        ("post", "/api/admin/depts"),
        ("patch", "/api/admin/depts/1"),
        ("delete", "/api/admin/depts/1"),
    ]:
        resp = await client.request(method, url, json={"name": "x"})
        assert resp.status_code == 403, (method, url, resp.text)


async def test_list_seeded_with_admin_only(client: AsyncClient):
    await login(client)  # admin，无种子部门
    resp = await client.get("/api/admin/depts")
    assert resp.status_code == 200
    assert resp.json() == {"items": []}


async def test_create_root_and_nested(client: AsyncClient):
    await login(client)
    root = await _create(client, "总部")
    assert root["path"] == f"/{root['id']}/"
    assert root["parent_id"] is None

    child = await _create(client, "研发部", parent_id=root["id"])
    assert child["path"] == f"/{root['id']}/{child['id']}/"
    assert child["parent_id"] == root["id"]

    grand = await _create(client, "后端组", parent_id=child["id"])
    assert grand["path"] == f"/{root['id']}/{child['id']}/{grand['id']}/"


async def test_list_sorted_by_path(client: AsyncClient):
    await login(client)
    root = await _create(client, "A")
    b = await _create(client, "B", parent_id=root["id"])
    await _create(client, "C", parent_id=b["id"])
    items = (await client.get("/api/admin/depts")).json()["items"]
    paths = [d["path"] for d in items]
    assert paths == sorted(paths)


async def test_create_unknown_parent_400(client: AsyncClient):
    await login(client)
    resp = await client.post("/api/admin/depts", json={"name": "x", "parent_id": 9999})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "unknown_parent"


async def test_patch_rename(client: AsyncClient):
    await login(client)
    d = await _create(client, "原名")
    resp = await client.patch(f"/api/admin/depts/{d['id']}", json={"name": "新名"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "新名"


async def test_patch_move_to_other_parent_updates_descendants(client: AsyncClient):
    await login(client)
    a = await _create(client, "A")
    b = await _create(client, "B")
    c = await _create(client, "C", parent_id=a["id"])
    d = await _create(client, "D", parent_id=c["id"])  # A → D → C

    # 把 c 挪到 B 下：d 路径应随之更新
    resp = await client.patch(f"/api/admin/depts/{c['id']}", json={"parent_id": b["id"]})
    assert resp.status_code == 200
    new_c = resp.json()
    assert new_c["parent_id"] == b["id"]
    assert new_c["path"] == f"/{b['id']}/{c['id']}/"

    # 验证 d 的 path 已被级联刷新
    items = {x["id"]: x for x in (await client.get("/api/admin/depts")).json()["items"]}
    assert items[d["id"]]["path"] == f"/{b['id']}/{c['id']}/{d['id']}/"


async def test_patch_move_to_top_level(client: AsyncClient):
    await login(client)
    root = await _create(client, "总部")
    child = await _create(client, "研发", parent_id=root["id"])
    resp = await client.patch(f"/api/admin/depts/{child['id']}", json={"parent_id": None})
    assert resp.status_code == 200
    assert resp.json()["parent_id"] is None
    assert resp.json()["path"] == f"/{child['id']}/"


async def test_patch_self_parent_400(client: AsyncClient):
    await login(client)
    d = await _create(client, "X")
    resp = await client.patch(f"/api/admin/depts/{d['id']}", json={"parent_id": d["id"]})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "self_parent"


async def test_patch_cycle_400(client: AsyncClient):
    await login(client)
    a = await _create(client, "A")
    b = await _create(client, "B", parent_id=a["id"])
    # 试图把 A 的父设为 B（A 已经是 B 的祖先 → 成环）
    resp = await client.patch(f"/api/admin/depts/{a['id']}", json={"parent_id": b["id"]})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "cycle"


async def test_patch_unknown_dept_404(client: AsyncClient):
    await login(client)
    resp = await client.patch("/api/admin/depts/9999", json={"name": "x"})
    assert resp.status_code == 404


async def test_delete_empty_ok(client: AsyncClient):
    await login(client)
    d = await _create(client, "to-delete")
    resp = await client.delete(f"/api/admin/depts/{d['id']}")
    assert resp.status_code == 204
    # 列表里没了
    items = (await client.get("/api/admin/depts")).json()["items"]
    assert all(x["id"] != d["id"] for x in items)


async def test_delete_has_children_409(client: AsyncClient):
    await login(client)
    parent = await _create(client, "P")
    await _create(client, "C", parent_id=parent["id"])
    resp = await client.delete(f"/api/admin/depts/{parent['id']}")
    assert resp.status_code == 409
    assert "children" in resp.json()["detail"]


async def test_delete_has_users_409(client: AsyncClient):
    await login(client)
    d = await _create(client, "财务部")
    # 创建一个引用该部门的用户
    async with SessionLocal() as s:
        from app.core.security import hash_password
        from app.models import User
        s.add(
            User(
                email="finance@x.com",
                name="F",
                password_hash=hash_password("x"),
                roles=["USER"],
                dept_id=d["id"],
            )
        )
        await s.commit()
    resp = await client.delete(f"/api/admin/depts/{d['id']}")
    assert resp.status_code == 409
    assert "users" in resp.json()["detail"]


async def test_delete_unknown_404(client: AsyncClient):
    await login(client)
    resp = await client.delete("/api/admin/depts/9999")
    assert resp.status_code == 404


async def test_delete_cleans_dept_apps(client: AsyncClient):
    """删除部门前应清理 dept 类型的 app_authorizations（FK 无 cascade）。"""
    await login(client)
    d = await _create(client, "to-del")
    await client.put(f"/api/admin/depts/{d['id']}/apps", json={"app_ids": [1, 2]})

    resp = await client.delete(f"/api/admin/depts/{d['id']}")
    assert resp.status_code == 204

    async with SessionLocal() as s:
        rows = (
            await s.execute(
                select(Department).where(Department.id == d["id"])
            )
        ).scalars().all()
        assert rows == []  # 部门已删

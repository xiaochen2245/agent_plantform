"""知识库端点（契约 v7）：透传 / 权限门 / 上游错误映射 / key 缺失 503。"""
import json

import httpx
import pytest
from httpx import AsyncClient

from app.dify.client import DifyClient
from app.dify.deps import get_dify
from tests.conftest import login
from tests.test_authz import mkuser

DATASET_LIST = {
    "total": 1,
    "has_more": False,
    "page": 1,
    "limit": 20,
    "data": [
        {
            "id": "ds-1",
            "name": "General Mode-ECO 1",
            "document_count": 2,
            "word_count": 128,
            "indexing_technique": "economy",
        }
    ],
}


def fake_dataset_client(
    captured: list | None = None,
    status: int = 200,
    body: dict | None = None,
) -> DifyClient:
    """按 URL 分流的最小 dataset 假身：默认回 DATASET_LIST。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            entry = {"url": str(request.url), "auth": request.headers.get("authorization")}
            ctype = request.headers.get("content-type", "")
            if ctype.startswith("application/json"):
                entry["json"] = json.loads(request.content.decode())
            else:
                entry["content"] = request.content
            captured.append(entry)
        if request.method == "DELETE":  # Dify 删除回 204 空体
            return httpx.Response(204)
        return httpx.Response(status, json=body if body is not None else DATASET_LIST)

    return DifyClient(base_url="http://fake-dify", transport=httpx.MockTransport(handler))


def _install(dify: DifyClient) -> None:
    from app.main import app

    app.dependency_overrides[get_dify] = lambda: dify


@pytest.fixture(autouse=True)
def _dataset_key(monkeypatch):
    monkeypatch.setenv("DIFY_DATASET_API_KEY", "dataset-test-key")


async def test_list_datasets_passthrough_with_dataset_key(client: AsyncClient):
    captured: list = []
    _install(fake_dataset_client(captured))
    await login(client)
    resp = await client.get("/api/kb/datasets")
    assert resp.status_code == 200
    assert resp.json() == DATASET_LIST
    # 鉴权头用的是 dataset key，而非 app key
    assert captured[0]["url"].startswith("http://fake-dify/v1/datasets?")
    assert captured[0]["auth"] == "Bearer dataset-test-key"


async def test_read_allowed_for_normal_user_write_forbidden(client: AsyncClient):
    """v8 后读也需授权：先授予 ds-1，读放行；写仍恒拒。"""
    _install(fake_dataset_client())
    uid = await mkuser("guest1@company.com")
    await login(client)  # admin 授权
    resp = await client.put(f"/api/admin/users/{uid}/datasets", json={"dataset_ids": ["ds-1"]})
    assert resp.status_code == 200

    await login(client, "guest1@company.com", "guest-pass-123")

    # 读：列表 + 命中测试 → 200
    assert (await client.get("/api/kb/datasets")).status_code == 200
    assert (
        await client.post("/api/kb/datasets/ds-1/retrieve", json={"query": "报销"})
    ).status_code == 200

    # 写：文本上传 / 文件上传 / 删除 → 403
    assert (
        await client.post(
            "/api/kb/datasets/ds-1/documents/text",
            json={"name": "n", "text": "t", "indexing_technique": "economy"},
        )
    ).status_code == 403
    assert (
        await client.post("/api/kb/datasets/ds-1/documents/file", files={"file": b"x"})
    ).status_code == 403
    assert (
        await client.delete("/api/kb/datasets/ds-1/documents/doc-1")
    ).status_code == 403


async def test_create_by_text_forwards_payload(client: AsyncClient):
    captured: list = []
    _install(fake_dataset_client(captured))
    await login(client)  # admin
    resp = await client.post(
        "/api/kb/datasets/ds-1/documents/text",
        json={"name": "报销政策", "text": "7 天内提交", "indexing_technique": "economy"},
    )
    assert resp.status_code == 201
    sent = captured[0]
    assert sent["url"].endswith("/document/create-by-text")
    assert sent["json"]["name"] == "报销政策"
    assert sent["json"]["indexing_technique"] == "economy"
    assert sent["json"]["process_rule"] == {"mode": "automatic"}


async def test_upload_file_forwarded_as_multipart(client: AsyncClient):
    captured: list = []
    _install(fake_dataset_client(captured))
    await login(client)
    resp = await client.post(
        "/api/kb/datasets/ds-1/documents/file",
        files={"file": ("policy.txt", "报销政策正文".encode(), "text/plain")},
        data={"indexing_technique": "economy"},
    )
    assert resp.status_code == 201
    body = captured[0]["content"].decode()
    # multipart 同时含 data JSON 配置与文件本体
    assert "indexing_technique" in body and '"mode": "automatic"' in body
    assert "报销政策正文" in body


async def test_upload_file_rejects_bad_mime(client: AsyncClient):
    _install(fake_dataset_client())
    await login(client)
    resp = await client.post(
        "/api/kb/datasets/ds-1/documents/file",
        files={"file": ("x.exe", b"MZ", "application/x-msdownload")},
    )
    assert resp.status_code == 400


async def test_admin_delete_document_204_empty_body(client: AsyncClient):
    """上游 204 无响应体：不得因 JSON 解析失败升级 502（e2e 实测回归）。"""
    _install(fake_dataset_client())
    await login(client)
    resp = await client.delete("/api/kb/datasets/ds-1/documents/doc-1")
    assert resp.status_code == 204


async def test_upstream_4xx_mapped_same_code(client: AsyncClient):
    _install(
        fake_dataset_client(
            status=400, body={"code": "invalid_param", "message": "indexing_technique is required."}
        )
    )
    await login(client)
    resp = await client.post(
        "/api/kb/datasets/ds-1/documents/text",
        json={"name": "n", "text": "t", "indexing_technique": "economy"},
    )
    assert resp.status_code == 400
    assert "indexing_technique" in resp.json()["detail"]


async def test_upstream_5xx_becomes_502(client: AsyncClient):
    _install(fake_dataset_client(status=500, body={"code": "internal_error", "message": "boom"}))
    await login(client)
    assert (await client.get("/api/kb/datasets")).status_code == 502


async def test_missing_dataset_key_503(client: AsyncClient, monkeypatch):
    monkeypatch.delenv("DIFY_DATASET_API_KEY", raising=False)
    _install(fake_dataset_client())
    await login(client)
    resp = await client.get("/api/kb/datasets")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "knowledge base service not configured"


# ── 契约 v8：知识库租户隔离（网关映射）────────────────────────────────


async def _mk_normal_user(client: AsyncClient, email: str) -> None:
    await login(client)  # admin 建号
    resp = await client.post(
        "/api/admin/users",
        json={"name": email.split("@")[0], "email": email, "password": "pass-12345"},
    )
    assert resp.status_code == 201
    await login(client, email, "pass-12345")


async def test_tenant_isolation_filters_list_and_blocks_scoped(
    client: AsyncClient,
):
    """无授权：列表过滤为空 + 作用域端点 403；授权后放行；admin 恒全量。"""
    _install(fake_dataset_client())
    await login(client)  # admin：恒全量
    body = await client.get("/api/kb/datasets")
    assert [d["name"] for d in body.json()["data"]] == ["General Mode-ECO 1"]

    # 未授权普通用户：列表被过滤为空，documents/retrieve 403
    await _mk_normal_user(client, "t1@company.com")
    body = await client.get("/api/kb/datasets")
    assert body.json()["data"] == []
    assert body.json()["total"] == 0
    assert (
        await client.get("/api/kb/datasets/ds-1/documents")
    ).status_code == 403
    assert (
        await client.post("/api/kb/datasets/ds-1/retrieve", json={"query": "q"})
    ).status_code == 403

    # admin 授权 ds-1 → 用户立即可见可用
    await login(client)
    resp = await client.put("/api/admin/users/2/datasets", json={"dataset_ids": ["ds-1"]})
    assert resp.status_code == 200 and resp.json()["dataset_ids"] == ["ds-1"]
    await login(client, "t1@company.com", "pass-12345")  # 已建号，直接重登
    body = await client.get("/api/kb/datasets")
    assert [d["name"] for d in body.json()["data"]] == ["General Mode-ECO 1"]
    assert (
        await client.post("/api/kb/datasets/ds-1/retrieve", json={"query": "q"})
    ).status_code == 200
    # 授权了 ds-1，其他库仍 403
    assert (
        await client.get("/api/kb/datasets/other-ds/documents")
    ).status_code == 403


async def test_admin_dataset_grants_crud(client: AsyncClient):
    """GET/PUT 用户级知识库授权：全量替换语义（空数组=清空）。"""
    _install(fake_dataset_client())
    await login(client)
    await client.post(
        "/api/admin/users",
        json={"name": "t2", "email": "t2@company.com", "password": "pass-12345"},
    )
    resp = await client.put(
        "/api/admin/users/2/datasets", json={"dataset_ids": ["ds-a", "ds-b"]}
    )
    assert resp.json()["dataset_ids"] == ["ds-a", "ds-b"]
    assert (await client.get("/api/admin/users/2/datasets")).json()[
        "dataset_ids"
    ] == ["ds-a", "ds-b"]
    # 全量替换：清空
    resp = await client.put("/api/admin/users/2/datasets", json={"dataset_ids": []})
    assert resp.json()["dataset_ids"] == []
    # 不存在的用户 404
    assert (
        await client.get("/api/admin/users/999/datasets")
    ).status_code == 404


# ── 契约 v9：建库/删库 + 库级授权管理 + 审计落库 ───────────────────────


async def test_create_and_delete_dataset_with_grant_cleanup(client: AsyncClient):
    captured: list = []
    _install(fake_dataset_client(captured))
    await login(client)

    resp = await client.post(
        "/api/kb/datasets", json={"name": "新建测试库", "indexing_technique": "economy"}
    )
    assert resp.status_code == 201
    assert captured[0]["json"] == {"name": "新建测试库", "indexing_technique": "economy"}

    # 授权一条 → 删库 → 授权行与审计同步
    await client.put("/api/admin/users/1/datasets", json={"dataset_ids": ["ds-1"]})
    resp = await client.delete("/api/kb/datasets/ds-1")
    assert resp.status_code == 204
    assert (await client.get("/api/admin/users/1/datasets")).json()["dataset_ids"] == []

    # 审计：建库 + 删库 + 用户级授权替换(grant via PUT 不审计，端点级 add/remove 才审计)
    audit = (await client.get("/api/kb/audit")).json()
    actions = [i["action"] for i in audit["items"]]
    assert "dataset_create" in actions and "dataset_delete" in actions
    assert audit["items"][0]["user"]  # 操作人姓名已 join


async def test_dataset_grants_crud_three_principals(client: AsyncClient):
    """库级授权视图 + 三态单条增删（幂等）+ 主体不存在 404。"""
    from sqlalchemy import select

    from app.db.session import SessionLocal
    from app.models.role import Role

    _install(fake_dataset_client())
    await login(client)

    # 准备：部门/角色/普通用户（直插，复用 authz 测试基建；种子库无部门行）
    from app.models.department import Department

    async with SessionLocal() as s:
        s.add(Department(name="审计测试部"))
        s.add(Role(code="GUEST2", name="访客2"))
        await s.flush()
        did = (await s.execute(select(Department.id).order_by(Department.id))).scalars().first()
        rid = (await s.execute(select(Role.id).where(Role.code == "GUEST2"))).scalar_one()
        await s.commit()
    uid = await mkuser("g9@company.com")

    base = "/api/kb/datasets/ds-1/grants"
    assert (await client.post(base, json={"principal_type": "dept", "principal_id": did})).status_code == 201
    assert (await client.post(base, json={"principal_type": "role", "principal_id": rid})).status_code == 201
    assert (await client.post(base, json={"principal_type": "user", "principal_id": uid})).status_code == 201
    # 幂等：重复添加 201，不报错
    assert (await client.post(base, json={"principal_type": "user", "principal_id": uid})).status_code == 201
    # 主体不存在 → 404
    assert (
        await client.post(base, json={"principal_type": "user", "principal_id": 99999})
    ).status_code == 404

    items = (await client.get(base)).json()["items"]
    kinds = {(i["principal_type"], i["principal_id"]) for i in items}
    assert {("user", uid), ("dept", did), ("role", rid)} <= kinds
    assert all(i["name"] for i in items)  # 名称已解析

    # 移除 → 列表少了；幂等移除仍 204
    assert (
        await client.delete(f"{base}/user/{uid}")
    ).status_code == 204
    items = (await client.get(base)).json()["items"]
    assert ("user", uid) not in {(i["principal_type"], i["principal_id"]) for i in items}
    assert (await client.delete(f"{base}/user/{uid}")).status_code == 204

    # 非 admin 碰授权管理 → 403
    await login(client, "g9@company.com", "guest-pass-123")
    assert (await client.get(base)).status_code == 403


async def test_doc_writes_audited(client: AsyncClient):
    """契约 v9：文档写路径（文本/文件/删除）全部落审计。"""
    _install(fake_dataset_client())
    await login(client)
    await client.post(
        "/api/kb/datasets/ds-1/documents/text",
        json={"name": "审计验证", "text": "内容", "indexing_technique": "economy"},
    )
    await client.post(
        "/api/kb/datasets/ds-1/documents/file",
        files={"file": ("a.txt", b"hello", "text/plain")},
        data={"indexing_technique": "economy"},
    )
    await client.delete("/api/kb/datasets/ds-1/documents/doc-1")
    actions = [i["action"] for i in (await client.get("/api/kb/audit")).json()["items"]]
    assert {"doc_create_text", "doc_create_file", "doc_delete"} <= set(actions)

"""RAG 网关端点：key 缺失 503 / 权限门 / 上传路由与解析触发 / 越权映射 404。"""
import json

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.main import app
from app.ragflow.client import RagflowClient
from app.ragflow.deps import get_ragflow
from app.ragflow.parsing import route_for
from tests.conftest import login
from tests.test_authz import mkuser


def fake_ragflow(
    captured: list | None = None,
    status: int = 200,
    body: dict | None = None,
    api_key: str = "ragflow-test-key",
) -> RagflowClient:
    """最小 RAGFlow 假身：按方法分流，默认回 200 空数据。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            entry = {
                "method": request.method,
                "url": str(request.url),
                "auth": request.headers.get("authorization"),
            }
            if request.content and "json" in request.headers.get("content-type", ""):
                try:
                    entry["body"] = json.loads(request.content.decode())
                except ValueError:
                    pass
            captured.append(entry)
        if status != 200:
            return httpx.Response(status, json=body or {"code": 102, "message": "You don't own the dataset x."})
        # 分流默认响应
        if request.url.path.endswith("/retrieval"):
            return httpx.Response(200, json={"code": 0, "data": {"chunks": [
                {"content": "评分表内容", "similarity": 0.52, "document_id": "d1"}
            ]}})
        if "/chunks/" in request.url.path:
            if request.method == "GET":
                return httpx.Response(200, json={"code": 0, "data": {
                    "id": "c-1", "content": "切片内容", "document_id": "doc-1",
                    "available": True, "important_keywords": ["埋深"], "positions": [[1, 2]],
                }})
            return httpx.Response(200, json={"code": 0})  # PATCH/DELETE 单切片
        if request.url.path.endswith("/chunks") and request.method == "GET":
            return httpx.Response(200, json={"code": 0, "data": {
                "chunks": [
                    {"id": "c-1", "content": "切片1", "document_id": "doc-1",
                     "available": True, "important_keywords": [], "positions": [[1]]}
                ],
                "total": 1,
            }})
        if "/documents" in request.url.path:
            if request.method == "POST":  # 上传
                return httpx.Response(200, json={"code": 0, "data": [
                    {"id": "doc-1", "name": "评分表.docx", "run": "UNSTART"}
                ]})
            return httpx.Response(200, json={"code": 0, "data": {"docs": [
                {"id": "doc-1", "name": "评分表.docx", "run": "DONE", "progress": 1.0}
            ]}})
        if request.url.path.endswith("/datasets") and request.method == "POST":
            return httpx.Response(200, json={"code": 0, "data": {"id": "ds-9"}})
        if request.url.path.endswith("/chunks"):
            return httpx.Response(200, json={"code": 0})
        return httpx.Response(200, json={"code": 0, "data": []})

    return RagflowClient(
        base_url="http://fake-ragflow",
        api_key=api_key,
        transport=httpx.MockTransport(handler),
    )


@pytest.fixture
def rag(captured: list | None = None) -> RagflowClient:
    return fake_ragflow(captured)


def _override(client: RagflowClient) -> None:
    app.dependency_overrides[get_ragflow] = lambda: client


async def test_requires_auth(client: AsyncClient):
    r = await client.get("/api/rag/datasets")
    assert r.status_code == 401


async def test_503_when_key_missing(client: AsyncClient):
    _override(fake_ragflow(api_key=""))
    await login(client)
    r = await client.get("/api/rag/datasets")
    assert r.status_code == 503


async def test_list_and_retrieval_as_user(client: AsyncClient):
    captured: list = []
    _override(fake_ragflow(captured))
    uid = await mkuser("reader@company.com", role_codes=("USER",))
    await login(client, email="reader@company.com", password="guest-pass-123")
    r = await client.get("/api/rag/datasets")
    assert r.status_code == 200
    r = await client.post("/api/rag/retrieval", json={
        "question": "分值", "dataset_ids": ["ds-1"], "top_n": 3,
    })
    assert r.status_code == 200
    assert r.json()["chunks"][0]["similarity"] == pytest.approx(0.52)
    assert captured[-1]["url"].endswith("/api/v1/retrieval")
    assert captured[-1]["auth"] == "Bearer ragflow-test-key"


async def test_create_dataset_needs_binding(client: AsyncClient):
    # 写权限=本租户登录用户；无部门绑定 → 依赖层 503
    await mkuser("reader2@company.com", role_codes=("USER",))
    await login(client, email="reader2@company.com", password="guest-pass-123")
    r = await client.post("/api/rag/datasets", json={"name": "x"})
    assert r.status_code == 503


async def test_create_dataset_and_upload_flow(client: AsyncClient, monkeypatch):
    captured: list = []
    monkeypatch.setattr("app.ragflow.router.spawn_autotag", lambda *a, **k: None)  # 后台轮询单测另测
    _override(fake_ragflow(captured))
    await login(client)  # admin
    r = await client.post("/api/rag/datasets", json={"name": "评分表库"})
    assert r.status_code == 201
    assert r.json()["id"] == "ds-9"

    r = await client.post(
        "/api/rag/datasets/ds-9/documents",
        files={"files": ("评分表.docx", b"fake-docx-bytes", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert r.status_code == 202
    assert r.json()["accepted"][0]["id"] == "doc-1"
    # 上传后自动触发解析
    parse_calls = [c for c in captured if c["url"].endswith("/chunks")]
    assert parse_calls, "parse must be triggered after upload"


async def test_upload_rejects_unsupported_type(client: AsyncClient):
    _override(fake_ragflow())
    await login(client)
    r = await client.post(
        "/api/rag/datasets/ds-9/documents",
        files={"files": ("恶意.exe", b"MZ", "application/octet-stream")},
    )
    assert r.status_code == 415


async def test_cross_tenant_denied_maps_404(client: AsyncClient):
    _override(fake_ragflow(status=200))  # body 携带业务拒绝
    await login(client)
    # 假身仅在 status!=200 时回业务拒绝；这里直接测 status 路径
    _override(fake_ragflow(status=403))
    r = await client.get("/api/rag/datasets/ds-other/documents")
    assert r.status_code == 404


def test_routing_table():
    assert route_for("评分表-技术标.DOCX") == "ragflow-deepdoc"
    assert route_for("scan.pdf") == "ragflow-deepdoc"
    with pytest.raises(ValueError):
        route_for("file.zip")


# ---- W2: 租户绑定 + 打标 + metadata 过滤 ----

from app.core import vault
from app.db.session import SessionLocal
from app.models.department import Department
from app.models.ragflow_binding import RagflowBinding
from app.ragflow.deps import _client_for_token
from app.ragflow.onboarding import ragflow_encrypt_password
from app.ragflow.tagging import ExtractedLabels, Tagger


def fake_provisioner_ok():
    class P:
        async def provision(self, email):
            return "ragflow-tok-1", "ds-default-1", "pw123"
        async def aclose(self):
            pass
    return P()


async def _mk_dept(name: str) -> int:
    async with SessionLocal() as s:
        d = Department(name=name)
        s.add(d)
        await s.flush()
        did = d.id
        await s.commit()
        return did


def test_vault_roundtrip():
    assert vault.decrypt(vault.encrypt("secret-中文-🔐")) == "secret-中文-🔐"


def test_ragflow_password_rsa_roundtrip(monkeypatch):
    """RSA(base64(pwd)) 再 base64；用生成的密钥对验证可解回 base64(pwd)。"""
    import base64
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    priv = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    from app.core.config import settings
    monkeypatch.setattr(settings, "RAGFLOW_PUBLIC_KEY", pem)
    enc = ragflow_encrypt_password("Spike#2026")
    plain = priv.decrypt(base64.b64decode(enc), padding.PKCS1v15())
    assert base64.b64decode(plain) == b"Spike#2026"


async def test_binding_requires_admin(client: AsyncClient):
    await mkuser("reader3@company.com", role_codes=("USER",))
    await login(client, email="reader3@company.com", password="guest-pass-123")
    r = await client.post("/api/rag/bindings", json={"department_id": 1})
    assert r.status_code == 403


async def test_create_binding_and_tenant_scoped_access(client: AsyncClient, monkeypatch):
    import app.ragflow.router as rag_router
    monkeypatch.setattr(rag_router, "RagflowProvisioner", fake_provisioner_ok)
    await login(client)  # admin
    did = await _mk_dept("给排水部")
    r = await client.post("/api/rag/bindings", json={"department_id": did})
    assert r.status_code == 201, r.text
    assert r.json()["default_dataset_id"] == "ds-default-1"

    # 列表可见
    r = await client.get("/api/rag/bindings")
    assert any(b["department_id"] == did for b in r.json()["bindings"])

    # 该部门用户 → 解析到租户 client；无部门用户 → 503
    calls = []
    class FakeClient:
        _api_key = "ragflow-tok-1"
        async def list_datasets(self, *a, **k):
            calls.append(a)
            return {"data": [{"id": "ds-default-1", "name": "default"}]}
    monkeypatch.setattr("app.ragflow.deps._client_for_token", lambda req, tok: FakeClient())
    uid = await mkuser("deptuser@company.com", role_codes=("USER",), dept_id=did)
    await login(client, email="deptuser@company.com", password="guest-pass-123")
    r = await client.get("/api/rag/datasets")
    assert r.status_code == 200 and r.json()["data"][0]["id"] == "ds-default-1"

    await mkuser("nodept@company.com", role_codes=("USER",))
    await login(client, email="nodept@company.com", password="guest-pass-123")
    r = await client.get("/api/rag/datasets")
    assert r.status_code == 503


async def test_tag_document_flow(client: AsyncClient, monkeypatch):
    _override(fake_ragflow())  # 有 key 的假身（绕过绑定解析）
    await login(client)
    captured = {}

    class FakeTagger(Tagger):
        async def extract(self, chunks):
            captured["chunks"] = len(chunks)
            return ExtractedLabels(project="XX管网改造", discipline="给排水",
                                   doc_type="设计审查单", date="2025",
                                   keywords=["管道埋深", "冻土线"])

    monkeypatch.setattr("app.ragflow.router.Tagger", FakeTagger)

    fake = fake_ragflow()
    # 让 list_chunks 返回非空
    def handler(request):
        if "/chunks" in request.url.path and request.method == "GET":
            return httpx.Response(200, json={"code": 0, "data": {"chunks": [
                {"content": "审查意见1：管道埋深未考虑冻土线"}] * 3}})
        if request.method == "PATCH":
            import json as _j
            captured["patch"] = _j.loads(request.content.decode())
            return httpx.Response(200, json={"code": 0})
        return httpx.Response(200, json={"code": 0, "data": []})
    fake._client._transport = httpx.MockTransport(handler)
    _override(fake)

    r = await client.post("/api/rag/datasets/ds-1/documents/doc-1/tag")
    assert r.status_code == 200, r.text
    meta = r.json()["meta_fields"]
    assert meta["discipline"] == "给排水" and "管道埋深" in meta["keywords"]
    assert captured["patch"]["meta_fields"]["project"] == "XX管网改造"


async def test_retrieval_metadata_condition_passthrough(client: AsyncClient):
    captured: list = []

    def handler(request):
        import json as _j
        captured.append(_j.loads(request.content.decode()))
        return httpx.Response(200, json={"code": 0, "data": {"chunks": []}})

    fake = fake_ragflow()
    fake._client._transport = httpx.MockTransport(handler)
    _override(fake)
    await login(client)
    r = await client.post("/api/rag/retrieval", json={
        "question": "经验", "dataset_ids": ["ds-1"],
        "metadata_condition": {"logic": "and",
            "conditions": [{"name": "discipline", "comparison_operator": "is", "value": "给排水"}]},
    })
    assert r.status_code == 200
    assert captured[-1]["metadata_condition"]["conditions"][0]["value"] == "给排水"


# ---- W3+: 问答代理 + 知识库 CRUD ----

from app.ragflow.client import RagflowClient


def fake_full_ragflow(captured: list, datasets: list[dict] | None = None) -> RagflowClient:
    def handler(request: httpx.Request) -> httpx.Response:
        entry = {"method": request.method, "url": str(request.url)}
        if request.content and "json" in request.headers.get("content-type", ""):
            try:
                entry["body"] = json.loads(request.content.decode())
            except ValueError:
                pass
        captured.append(entry)
        if request.url.path.endswith("/datasets") and request.method == "GET":
            return httpx.Response(200, json={"code": 0, "data": datasets or []})
        if request.url.path.endswith("/chats") and request.method == "GET":
            return httpx.Response(200, json={"code": 0, "data": {"chats": []}})
        if request.url.path.endswith("/chats") and request.method == "POST":
            return httpx.Response(200, json={"code": 0, "data": {"id": "chat-9"}})
        return httpx.Response(200, json={"code": 0, "data": []})
    return RagflowClient(base_url="http://f", api_key="k", transport=httpx.MockTransport(handler))


async def test_chat_assistant_endpoint(client: AsyncClient):
    captured: list = []
    _override(fake_full_ragflow(captured))
    await login(client)
    r = await client.get("/api/rag/chat/assistant")
    assert r.status_code == 200 and r.json()["chat_id"] == "chat-9"
    # 建 assistant 前应先绑默认 chat 模型
    methods = [(c["method"], c["url"].split("/v1/")[-1].split("?")[0]) for c in captured]
    assert ("PATCH", "models/default") in methods


async def test_dataset_crud_endpoints(client: AsyncClient):
    captured: list = []
    _override(fake_full_ragflow(captured))
    await login(client)
    r = await client.request("DELETE", "/api/rag/datasets/ds-1")
    assert r.status_code == 204
    r = await client.patch("/api/rag/datasets/ds-1", json={"name": "新名"})
    assert r.status_code == 200
    r = await client.request("DELETE", "/api/rag/datasets/ds-1/documents", json={"ids": ["d1"]})
    assert r.status_code == 204
    assert any(c["method"] == "DELETE" and c["url"].endswith("/datasets/ds-1/documents") for c in captured)


# ---- #27 自动打标：上传后轮询 DONE 自动写 metadata ----


async def test_upload_spawns_autotag(client: AsyncClient, monkeypatch):
    spawned: list = []
    monkeypatch.setattr("app.ragflow.router.spawn_autotag",
                        lambda c, ds, ids, user_id: spawned.append((ds, ids, user_id)))
    _override(fake_ragflow())
    await login(client)
    admin_id = 1  # 种子管理员固定首行 id
    r = await client.post(
        "/api/rag/datasets/ds-1/documents",
        files={"files": ("评分表.docx", b"fake-docx-bytes",
                         "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert r.status_code == 202, r.text
    assert spawned == [("ds-1", ["doc-1"], admin_id)], "upload must schedule autotag w/ user"


class _AutotagFake:
    """直接喂 tag_when_done 的最小假身：run 可控，调用可追。"""

    def __init__(self, run: str):
        self.run = run
        self.calls: list[str] = []

    async def list_documents(self, dataset_id: str, page: int = 1) -> list[dict]:
        self.calls.append("list")
        return [{"id": "d1", "run": self.run}] if page == 1 else []

    async def list_chunks(self, dataset_id: str, document_id: str, **k) -> list[dict]:
        self.calls.append("chunks")
        return [{"content": "审查意见：管道埋深未考虑冻土线"}]

    async def update_document_meta(self, dataset_id: str, document_id: str, meta: dict) -> None:
        self.calls.append(("meta", meta))


async def test_tag_when_done_writes_meta(monkeypatch):
    from app.ragflow import autotag

    class FakeTagger(Tagger):
        async def extract(self, chunks):
            return ExtractedLabels(project="XX管网改造", discipline="给排水")

    monkeypatch.setattr(autotag, "Tagger", FakeTagger)
    fake = _AutotagFake("DONE")
    await autotag.tag_when_done(fake, "ds-1", "d1", user_id=1)
    metas = [c for c in fake.calls if isinstance(c, tuple)]
    assert metas and metas[0][1]["project"] == "XX管网改造"


async def test_tag_when_done_fail_and_timeout(monkeypatch):
    from app.ragflow import autotag

    # 解析失败 → 放弃，不拉 chunks 不写 meta
    fake = _AutotagFake("FAIL")
    await autotag.tag_when_done(fake, "ds-1", "d1", user_id=1)
    assert fake.calls == ["list"]

    # 一直 RUNNING + 超时 0 → 立即放弃
    fake = _AutotagFake("RUNNING")
    monkeypatch.setattr(autotag, "POLL_TIMEOUT", 0)
    await autotag.tag_when_done(fake, "ds-1", "d1", user_id=1)
    assert fake.calls == ["list"]


# ---- #28 写操作审计：每写一单 + admin 可查 / 非 admin 403 ----


async def test_rag_write_ops_audited(client: AsyncClient, monkeypatch):
    monkeypatch.setattr("app.ragflow.router.spawn_autotag", lambda *a, **k: None)
    fake = fake_ragflow()

    def handler(request):
        if "/chunks" in request.url.path and request.method == "GET":
            return httpx.Response(200, json={"code": 0, "data": {"chunks": [
                {"content": "审查意见：管道埋深"}] * 3}})
        return httpx.Response(200, json={"code": 0, "data": []})

    fake._client._transport = httpx.MockTransport(handler)
    _override(fake)
    await login(client)
    docx = ("评分表.docx", b"x",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    await client.post("/api/rag/datasets", json={"name": "库1"})
    await client.post("/api/rag/datasets/ds-1/documents", files={"files": docx})
    await client.post("/api/rag/datasets/ds-1/documents/doc-1/tag")
    await client.request("DELETE", "/api/rag/datasets/ds-1/documents", json={"ids": ["doc-1"]})
    await client.request("DELETE", "/api/rag/datasets/ds-1")

    r = await client.get("/api/rag/audit")
    assert r.status_code == 200, r.text
    actions = [l["action"] for l in r.json()["logs"]]
    for expected in ("dataset.create", "doc.upload", "doc.tag", "doc.delete", "dataset.delete"):
        assert expected in actions, f"{expected} missing in {actions}"
    tag_log = next(l for l in r.json()["logs"] if l["action"] == "doc.tag")
    assert tag_log["user_id"] == 1 and tag_log["user_name"] == "平台管理员"
    assert "doc-1" in tag_log["detail"]

    # user_id 过滤
    r = await client.get("/api/rag/audit", params={"user_id": 999})
    assert r.json()["logs"] == []


async def test_rag_audit_requires_admin(client: AsyncClient):
    await mkuser("plain@company.com", role_codes=("USER",))
    await login(client, email="plain@company.com", password="guest-pass-123")
    r = await client.get("/api/rag/audit")
    assert r.status_code == 403


async def test_autotag_writes_audit(monkeypatch):
    from app.db.session import SessionLocal
    from app.models.rag_audit import RagAuditLog
    from app.ragflow import autotag
    from sqlalchemy import select

    class FakeTagger(Tagger):
        async def extract(self, chunks):
            return ExtractedLabels(project="XX管网")

    monkeypatch.setattr(autotag, "Tagger", FakeTagger)
    fake = _AutotagFake("DONE")
    await autotag.tag_when_done(fake, "ds-1", "d1", user_id=7)
    async with SessionLocal() as s:
        row = await s.scalar(select(RagAuditLog).where(RagAuditLog.action == "doc.tag"))
    assert row is not None and row.user_id == 7
    assert "autotag" in (row.detail or "")


# ---- #38 会话持久化：创建/列表/读消息/全量同步/软删 + 越权隔离 ----


async def test_chat_session_lifecycle(client: AsyncClient):
    await login(client)
    r = await client.post("/api/rag/chat/sessions", json={"title": ""})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]

    # 全量同步两轮
    turns = [
        {"role": "user", "content": "管道埋深要求？"},
        {"role": "assistant", "content": "需考虑冻土线。"},
    ]
    r = await client.put(f"/api/rag/chat/sessions/{sid}/messages",
                         json={"messages": turns, "title": "管道问题"})
    assert r.status_code == 200 and r.json()["message_count"] == 2

    # 再同步一轮（幂等重写而非追加）
    turns.append({"role": "user", "content": "依据哪份审查单？"})
    r = await client.put(f"/api/rag/chat/sessions/{sid}/messages",
                         json={"messages": turns, "title": "管道问题"})
    assert r.json()["message_count"] == 3

    r = await client.get(f"/api/rag/chat/sessions/{sid}/messages")
    assert [m["content"] for m in r.json()["messages"]] == [t["content"] for t in turns]

    r = await client.get("/api/rag/chat/sessions")
    assert r.status_code == 200
    s = r.json()["sessions"][0]
    assert s["title"] == "管道问题" and s["message_count"] == 3

    # 软删后不可见不可读
    r = await client.delete(f"/api/rag/chat/sessions/{sid}")
    assert r.status_code == 204
    assert (await client.get("/api/rag/chat/sessions")).json()["sessions"] == []
    assert (await client.get(f"/api/rag/chat/sessions/{sid}/messages")).status_code == 404


async def test_chat_session_isolation_and_validation(client: AsyncClient):
    await login(client)  # admin 建
    sid = (await client.post("/api/rag/chat/sessions", json={})).json()["id"]

    await mkuser("peer@company.com", role_codes=("USER",))
    await login(client, email="peer@company.com", password="guest-pass-123")
    assert (await client.get(f"/api/rag/chat/sessions/{sid}/messages")).status_code == 404
    assert (await client.put(f"/api/rag/chat/sessions/{sid}/messages",
                             json={"messages": [{"role": "user", "content": "x"}]})).status_code == 404

    # 非法 app_id / 坏 uuid / 坏 role
    assert (await client.post("/api/rag/chat/sessions", json={"app_id": 999})).status_code == 404
    assert (await client.get("/api/rag/chat/sessions/not-a-uuid/messages")).status_code == 404
    r = await client.post("/api/rag/chat/sessions", json={})
    sid2 = r.json()["id"]
    assert (await client.put(f"/api/rag/chat/sessions/{sid2}/messages",
                             json={"messages": [{"role": "system", "content": "x"}]})).status_code == 422


# ---- #29 document_ids 白名单通道：透传 + schema 拒收 + 策略缝 ----


async def test_retrieval_document_ids_passthrough(client: AsyncClient, monkeypatch):
    """策略返回白名单 → 必须透传 RAGFlow（capture 断言）。"""
    import app.ragflow.policy as policy
    captured: list = []

    def handler(request):
        import json as _j
        captured.append(_j.loads(request.content.decode()))
        return httpx.Response(200, json={"code": 0, "data": {"chunks": []}})

    fake = fake_ragflow()
    fake._client._transport = httpx.MockTransport(handler)
    _override(fake)
    await login(client)

    async def fake_visible(db, user, dataset_ids):
        return ["doc-a", "doc-b"]

    monkeypatch.setattr(policy, "visible_document_ids", fake_visible)
    # router 持有直接引用，需一并替换（缝在 policy 模块，路由 import 名替换）
    import app.ragflow.router as rag_router
    monkeypatch.setattr(rag_router, "visible_document_ids", fake_visible, raising=False)

    r = await client.post("/api/rag/retrieval", json={
        "question": "经验", "dataset_ids": ["ds-1"], "document_ids": ["hack"]})
    assert r.status_code == 200
    body = captured[-1]
    assert body.get("document_ids") == ["doc-a", "doc-b"], "服务端白名单必须透传"
    assert "document_ids" not in str(body.get("document_ids")) or body["document_ids"] != ["hack"]


async def test_retrieval_rejects_client_document_ids(client: AsyncClient):
    """schema 不收客户端 document_ids（可传=越权面）——多余字段被忽略且不透传。"""
    captured: list = []

    def handler(request):
        import json as _j
        captured.append(_j.loads(request.content.decode()))
        return httpx.Response(200, json={"code": 0, "data": {"chunks": []}})

    fake = fake_ragflow()
    fake._client._transport = httpx.MockTransport(handler)
    _override(fake)
    await login(client)
    # pydantic 默认忽略额外字段 → 请求成功但 body 里绝不能出现客户端注入的 document_ids
    r = await client.post("/api/rag/retrieval", json={
        "question": "经验", "dataset_ids": ["ds-1"], "document_ids": ["hack"]})
    assert r.status_code == 200
    assert "document_ids" not in captured[-1], "策略为 None 时不得透传任何 document_ids"


# ---- P0：检索参数化 / 切片通道 / 全量库绑定 / SSE 引用透传 ----


async def test_retrieval_params_passthrough(client: AsyncClient):
    """P0-②：检索台参数透传 + top_n→page_size 映射；弃用的 top_k 不得出现。"""
    captured: list = []

    def handler(request):
        captured.append(json.loads(request.content.decode()))
        return httpx.Response(200, json={"code": 0, "data": {"chunks": [{
            "id": "c1", "content": "命中切片", "document_id": "d1",
            "document_keyword": "评分表.docx", "dataset_id": "ds-1",
            "similarity": 0.9, "term_similarity": 0.8, "vector_similarity": 0.7,
            "positions": [[1, 2]], "highlight": "<em>命中</em>切片",
        }]}})

    fake = fake_ragflow()
    fake._client._transport = httpx.MockTransport(handler)
    _override(fake)
    await login(client)
    r = await client.post("/api/rag/retrieval", json={
        "question": "埋深", "dataset_ids": ["ds-1"],
        "top_n": 7, "similarity_threshold": 0.3, "vector_similarity_weight": 0.6,
        "rerank_id": "builtin", "keyword": True, "highlight": True,
    })
    assert r.status_code == 200, r.text
    body = captured[-1]
    assert body["page_size"] == 7, "top_n 必须映射为引擎 page_size"
    assert "top_k" not in body and "knn_top_k" not in body, "弃用参数不得透传"
    assert body["similarity_threshold"] == 0.3
    assert body["vector_similarity_weight"] == 0.6
    assert body["rerank_id"] == "builtin"
    assert body["keyword"] is True and body["highlight"] is True
    # 响应全字段（引用溯源依赖）
    c = r.json()["chunks"][0]
    assert c["document_keyword"] == "评分表.docx" and c["term_similarity"] == 0.8
    assert c["vector_similarity"] == 0.7 and c["positions"] == [[1, 2]]
    assert c["highlight"] == "<em>命中</em>切片" and c["id"] == "c1"


async def test_chat_assistant_binds_all_datasets(client: AsyncClient):
    """P0-0：新建 assistant 必须绑定全量库（原 ids[:1] 只绑首库）。"""
    captured: list = []
    _override(fake_full_ragflow(captured, datasets=[
        {"id": "ds-1", "name": "库1"}, {"id": "ds-2", "name": "库2"},
    ]))
    await login(client)
    r = await client.get("/api/rag/chat/assistant")
    assert r.status_code == 200 and r.json()["chat_id"] == "chat-9"
    create = next(c for c in captured if c["method"] == "POST" and c["url"].endswith("/chats"))
    assert set(create["body"]["dataset_ids"]) == {"ds-1", "ds-2"}


async def test_chat_assistant_syncs_stale_binding(client: AsyncClient):
    """P0-0：已有 assistant 绑定漂移（只绑首库）→ 复用时 PUT 同步为全量。"""
    captured: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append({"method": request.method, "url": str(request.url),
                         "body": json.loads(request.content.decode()) if request.content else None})
        if request.url.path.endswith("/datasets") and request.method == "GET":
            return httpx.Response(200, json={"code": 0, "data": [
                {"id": "ds-1", "name": "库1"}, {"id": "ds-2", "name": "库2"}]})
        if request.url.path.endswith("/chats") and request.method == "GET":
            return httpx.Response(200, json={"code": 0, "data": {"chats": [
                {"id": "chat-1", "name": "portal-assistant", "dataset_ids": ["ds-1"]}]}})
        return httpx.Response(200, json={"code": 0, "data": []})

    _override(RagflowClient(base_url="http://f", api_key="k",
                            transport=httpx.MockTransport(handler)))
    await login(client)
    r = await client.get("/api/rag/chat/assistant")
    assert r.status_code == 200 and r.json()["chat_id"] == "chat-1"
    put = next(c for c in captured if c["method"] == "PUT" and "/chats/" in c["url"])
    assert set(put["body"]["dataset_ids"]) == {"ds-1", "ds-2"}


async def test_chat_completions_sse_reference_full_fields(client: AsyncClient):
    """P0-①：SSE 引用全字段透传 + document_keyword→document_name 映射，不截断。"""
    long_content = "审查意见：管道埋深未考虑冻土线" * 10
    delta_line = 'data: {"choices":[{"delta":{"content":"答案增量"}}]}'.encode()
    ref_chunk = json.dumps({"content": long_content, "document_id": "d1",
                            "document_keyword": "评分表.docx", "dataset_id": "ds-1",
                            "similarity": 0.91, "positions": [[3, 1]]}, ensure_ascii=False).encode()
    ref_line = b'data: {"choices":[{"message":{"reference":{"chunks":[' + ref_chunk + b']}}}]}\n\n'
    sse = delta_line + b'\n\n' + ref_line + b'data: [DONE]\n\n'

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/datasets") and request.method == "GET":
            return httpx.Response(200, json={"code": 0, "data": [{"id": "ds-1", "name": "库1"}]})
        if request.url.path.endswith("/chats") and request.method == "GET":
            return httpx.Response(200, json={"code": 0, "data": {"chats": [
                {"id": "chat-1", "name": "portal-assistant", "dataset_ids": ["ds-1"]}]}})
        if "/openai/" in request.url.path:
            return httpx.Response(200, content=sse)
        return httpx.Response(200, json={"code": 0, "data": []})

    _override(RagflowClient(base_url="http://f", api_key="k",
                            transport=httpx.MockTransport(handler)))
    await login(client)
    async with client.stream("POST", "/api/rag/chat/completions",
                             json={"messages": [{"role": "user", "content": "管道埋深要求？"}]}) as r:
        assert r.status_code == 200
        text = (await r.aread()).decode()
    # 增量帧原样
    assert '"content":"答案增量"' in text
    # 引用帧：document_name 已映射、全文不截断、得分/位置透传
    ref_line = next(l for l in text.split("\n") if "reference" in l)
    ref = json.loads(ref_line[5:])
    chunk = ref["choices"][0]["message"]["reference"]["chunks"][0]
    assert chunk["document_name"] == "评分表.docx"
    assert chunk["content"] == long_content
    assert chunk["similarity"] == 0.91 and chunk["positions"] == [[3, 1]]
    assert "data: [DONE]" in text


async def test_chunk_endpoints_authz_and_audit(client: AsyncClient):
    """P0-③：切片 list/get 全员；patch/delete 仅 ADMIN；写操作入审计。"""
    captured: list = []
    fake = fake_ragflow(captured)
    _override(fake)
    await login(client)  # 种子管理员

    r = await client.get("/api/rag/datasets/ds-1/documents/doc-1/chunks",
                         params={"keywords": "埋深", "page_size": 50})
    assert r.status_code == 200, r.text
    assert r.json()["chunks"][0]["id"] == "c-1" and r.json()["total"] == 1

    r = await client.get("/api/rag/datasets/ds-1/documents/doc-1/chunks/c-1")
    assert r.status_code == 200 and r.json()["important_keywords"] == ["埋深"]

    r = await client.patch("/api/rag/datasets/ds-1/documents/doc-1/chunks/c-1",
                           json={"content": "修正后的切片", "available": True})
    assert r.status_code == 200, r.text
    patch_calls = [c for c in captured if c["method"] == "PATCH" and "/chunks/c-1" in c["url"]]
    assert patch_calls and patch_calls[0]["body"]["content"] == "修正后的切片"

    r = await client.delete("/api/rag/datasets/ds-1/documents/doc-1/chunks/c-1")
    assert r.status_code == 204
    delete_calls = [c for c in captured if c["method"] == "DELETE" and "/chunks" in c["url"]]
    assert delete_calls and delete_calls[0]["body"]["chunk_ids"] == ["c-1"]

    # 审计行
    r = await client.get("/api/rag/audit")
    actions = [l["action"] for l in r.json()["logs"]]
    assert "chunk.update" in actions and "chunk.delete" in actions

    # 非 admin：读可、写拒
    await mkuser("chunkreader@company.com", role_codes=("USER",))
    await login(client, email="chunkreader@company.com", password="guest-pass-123")
    assert (await client.get("/api/rag/datasets/ds-1/documents/doc-1/chunks")).status_code == 200
    assert (await client.patch("/api/rag/datasets/ds-1/documents/doc-1/chunks/c-1",
                               json={"content": "x"})).status_code == 403
    assert (await client.delete("/api/rag/datasets/ds-1/documents/doc-1/chunks/c-1")).status_code == 403


async def test_parse_document_endpoint(client: AsyncClient):
    """P0-③：重解析触发（登录用户，对齐上传口径）+ 审计。"""
    captured: list = []
    _override(fake_ragflow(captured))
    await mkuser("parseuser@company.com", role_codes=("USER",))
    await login(client, email="parseuser@company.com", password="guest-pass-123")
    r = await client.post("/api/rag/datasets/ds-1/documents/doc-1/parse")
    assert r.status_code == 202, r.text
    parse_calls = [c for c in captured
                   if c["method"] == "POST" and c["url"].endswith("/datasets/ds-1/chunks")]
    assert parse_calls and parse_calls[0]["body"]["document_ids"] == ["doc-1"]
    # 普通用户的写审计也入流水（admin 才能查，换管理员查）
    await login(client)
    r = await client.get("/api/rag/audit")
    assert "doc.parse" in [l["action"] for l in r.json()["logs"]]

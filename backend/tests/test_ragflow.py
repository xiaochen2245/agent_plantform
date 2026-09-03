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
            captured.append(
                {
                    "method": request.method,
                    "url": str(request.url),
                    "auth": request.headers.get("authorization"),
                }
            )
        if status != 200:
            return httpx.Response(status, json=body or {"code": 102, "message": "You don't own the dataset x."})
        # 分流默认响应
        if request.url.path.endswith("/retrieval"):
            return httpx.Response(200, json={"code": 0, "data": {"chunks": [
                {"content": "评分表内容", "similarity": 0.52, "document_id": "d1"}
            ]}})
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
        "question": "分值", "dataset_ids": ["ds-1"], "top_k": 3,
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
    monkeypatch.setattr("app.ragflow.router.spawn_autotag", lambda *a: None)  # 后台轮询单测另测
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


def fake_full_ragflow(captured: list) -> RagflowClient:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append({"method": request.method, "url": str(request.url)})
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
                        lambda c, ds, ids: spawned.append((ds, ids)))
    _override(fake_ragflow())
    await login(client)
    r = await client.post(
        "/api/rag/datasets/ds-1/documents",
        files={"files": ("评分表.docx", b"fake-docx-bytes",
                         "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert r.status_code == 202, r.text
    assert spawned == [("ds-1", ["doc-1"])], "upload must schedule autotag for each doc"


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
    await autotag.tag_when_done(fake, "ds-1", "d1")
    metas = [c for c in fake.calls if isinstance(c, tuple)]
    assert metas and metas[0][1]["project"] == "XX管网改造"


async def test_tag_when_done_fail_and_timeout(monkeypatch):
    from app.ragflow import autotag

    # 解析失败 → 放弃，不拉 chunks 不写 meta
    fake = _AutotagFake("FAIL")
    await autotag.tag_when_done(fake, "ds-1", "d1")
    assert fake.calls == ["list"]

    # 一直 RUNNING + 超时 0 → 立即放弃
    fake = _AutotagFake("RUNNING")
    monkeypatch.setattr(autotag, "POLL_TIMEOUT", 0)
    await autotag.tag_when_done(fake, "ds-1", "d1")
    assert fake.calls == ["list"]

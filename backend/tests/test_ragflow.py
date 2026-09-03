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


async def test_create_dataset_admin_only(client: AsyncClient):
    _override(fake_ragflow())
    await mkuser("reader2@company.com", role_codes=("USER",))
    await login(client, email="reader2@company.com", password="guest-pass-123")
    r = await client.post("/api/rag/datasets", json={"name": "x"})
    assert r.status_code == 403


async def test_create_dataset_and_upload_flow(client: AsyncClient):
    captured: list = []
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

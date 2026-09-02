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
    _install(fake_dataset_client())
    await mkuser("guest1@company.com")
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

"""RAGFlow v0.26 RESTful API 瘦客户端。

- AsyncClient 生命周期归 app.main lifespan（进程级单例，同 DifyClient 先例）
- transport 可注入：测试用 httpx.MockTransport
- 端点为 v0.26.4 实测路由（见 2026-09-03 spike）：
  POST /api/v1/datasets、GET /api/v1/datasets、
  POST /api/v1/datasets/{id}/documents（multipart 上传）、
  POST /api/v1/datasets/{id}/chunks（触发解析）、
  GET  /api/v1/datasets/{id}/documents、POST /api/v1/retrieval
"""
import httpx

from app.core.config import settings

# 上传/解析触发可能排队；检索要等 embedding。与 Dify 客户端同档超时。
RAGFLOW_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


class RagflowError(Exception):
    """上游 4xx/5xx 或业务 code!=0：携带 HTTP 状态与 message 供路由层映射。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class RagflowClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = (base_url or settings.RAGFLOW_BASE_URL).rstrip("/")
        self._api_key = api_key if api_key is not None else settings.RAGFLOW_API_KEY
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=RAGFLOW_TIMEOUT,
            transport=transport,
            # 内网引擎：绕过 http(s)_proxy 环境变量（同 DifyClient 先例）
            trust_env=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kw: object) -> dict:
        headers = kw.pop("headers", {}) or {}
        headers["Authorization"] = f"Bearer {self._api_key}"
        resp = await self._client.request(method, path, headers=headers, **kw)  # type: ignore[arg-type]
        if resp.status_code >= 400:
            raise RagflowError(resp.status_code, resp.text[:500])
        try:
            body = resp.json()
        except ValueError as e:
            raise RagflowError(502, f"ragflow returned non-json: {resp.text[:200]}") from e
        if body.get("code") not in (0, None):
            raise RagflowError(502, str(body.get("message") or body.get("code")))
        return body

    # ---- datasets ----

    async def create_dataset(self, name: str, description: str = "") -> dict:
        """返回 data 字段（含 id）。v0.26 的 id 在 data.id 而非顶层（spike 实测）。"""
        body = await self._request(
            "POST", "/api/v1/datasets", json={"name": name, "description": description}
        )
        return body.get("data") or {}

    async def list_datasets(self, page: int = 1, page_size: int = 30) -> dict:
        body = await self._request(
            "GET", "/api/v1/datasets", params={"page": page, "page_size": page_size}
        )
        return body

    # ---- documents ----

    async def upload_documents(
        self, dataset_id: str, files: list[tuple[str, bytes, str]]
    ) -> list[dict]:
        """files: [(filename, content, mime), ...]；返回文档对象列表（含 id/run）。"""
        files_field = [
            ("file", (name, content, mime)) for name, content, mime in files
        ]
        body = await self._request(
            "POST", f"/api/v1/datasets/{dataset_id}/documents", files=files_field
        )
        data = body.get("data") or []
        return data if isinstance(data, list) else [data]

    async def trigger_parse(self, dataset_id: str, document_ids: list[str]) -> None:
        await self._request(
            "POST",
            f"/api/v1/datasets/{dataset_id}/chunks",
            json={"document_ids": document_ids},
        )

    async def list_documents(self, dataset_id: str, page: int = 1, page_size: int = 30) -> list[dict]:
        """v0.26 列表形状为 data.docs[]（spike 实测）。"""
        body = await self._request(
            "GET",
            f"/api/v1/datasets/{dataset_id}/documents",
            params={"page": page, "page_size": page_size},
        )
        data = body.get("data") or {}
        return data.get("docs", []) if isinstance(data, dict) else data

    async def list_chunks(
        self, dataset_id: str, document_id: str, page: int = 1, page_size: int = 100
    ) -> list[dict]:
        """v0.26+ page_size 上限 100（超限报错）。"""
        body = await self._request(
            "GET",
            f"/api/v1/datasets/{dataset_id}/documents/{document_id}/chunks",
            params={"page": page, "page_size": min(page_size, 100)},
        )
        data = body.get("data") or {}
        return data.get("chunks", []) if isinstance(data, dict) else []

    async def update_document_meta(
        self, dataset_id: str, document_id: str, meta_fields: dict
    ) -> None:
        await self._request(
            "PATCH",
            f"/api/v1/datasets/{dataset_id}/documents/{document_id}",
            json={"meta_fields": meta_fields},
        )

    # ---- retrieval ----

    async def retrieve(
        self, question: str, dataset_ids: list[str], top_k: int = 5,
        metadata_condition: dict | None = None,
    ) -> dict:
        """返回 data（chunks 在 data.chunks[]，含 content/similarity）。"""
        body: dict = {"question": question, "dataset_ids": dataset_ids, "page_size": top_k}
        if metadata_condition:
            body["metadata_condition"] = metadata_condition
        payload = await self._request("POST", "/api/v1/retrieval", json=body)
        return payload.get("data") or {}

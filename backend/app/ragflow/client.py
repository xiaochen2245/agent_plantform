"""RAGFlow v0.26 RESTful API 瘦客户端。

- AsyncClient 生命周期归 app.main lifespan（进程级单例，同 DifyClient 先例）
- transport 可注入：测试用 httpx.MockTransport
- 端点为 v0.26.4 实测路由（见 2026-09-03 spike）：
  POST /api/v1/datasets、GET /api/v1/datasets、
  POST /api/v1/datasets/{id}/documents（multipart 上传）、
  POST /api/v1/datasets/{id}/chunks（触发解析）、
  GET  /api/v1/datasets/{id}/documents、POST /api/v1/retrieval
"""
import json
import logging

import httpx

from app.core.config import settings

_logger = logging.getLogger("app.ragflow.client")

# 上传/解析触发可能排队；检索要等 embedding。与 Dify 客户端同档超时。
RAGFLOW_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


def _map_reference_chunk(c: object) -> object:
    """引用切片透传：补 document_name（引擎面叫 document_keyword），其余原样不截断。"""
    if isinstance(c, dict) and not c.get("document_name") and c.get("document_keyword"):
        c["document_name"] = c["document_keyword"]
    return c


def _find_sse_reference(obj: dict) -> dict | None:
    choices = obj.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else {}
    ref = (choice.get("delta") or {}).get("reference") or (choice.get("message") or {}).get("reference")
    return ref if isinstance(ref, dict) else None


def _map_sse_reference(obj: dict) -> dict:
    ref = _find_sse_reference(obj)
    if ref is None:
        return obj
    chunks = ref.get("chunks")
    if isinstance(chunks, list):
        ref["chunks"] = [_map_reference_chunk(c) for c in chunks]
    elif isinstance(chunks, dict):
        ref["chunks"] = {k: _map_reference_chunk(v) for k, v in chunks.items()}
    return obj


def _reemit_sse_line(line: bytes) -> bytes:
    """按行重发 SSE：仅含 reference 的 data 帧重序列化（补全字段），其余帧字节原样。"""
    if not line.startswith(b"data:"):
        return line + b"\n"
    payload = line[5:].strip()
    if not payload or payload == b"[DONE]":
        return line + b"\n"
    try:
        obj = json.loads(payload)
    except ValueError:
        return line + b"\n"
    if not isinstance(obj, dict) or _find_sse_reference(obj) is None:
        return line + b"\n"
    return b"data: " + json.dumps(_map_sse_reference(obj), ensure_ascii=False).encode() + b"\n"


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

    # ---- datasets CRUD ----

    async def delete_dataset(self, dataset_id: str) -> None:
        await self._request("DELETE", f"/api/v1/datasets/{dataset_id}")

    async def update_dataset(self, dataset_id: str, name: str | None = None, description: str | None = None) -> None:
        body = {k: v for k, v in {"name": name, "description": description}.items() if v is not None}
        await self._request("PUT", f"/api/v1/datasets/{dataset_id}", json=body)

    async def delete_documents(self, dataset_id: str, document_ids: list[str]) -> None:
        await self._request(
            "DELETE",
            f"/api/v1/datasets/{dataset_id}/documents",
            json={"ids": document_ids},
        )

    # ---- chat assistant（问答：检索+LLM+引用一站式） ----

    async def list_chats(self) -> list[dict]:
        body = await self._request("GET", "/api/v1/chats")
        data = body.get("data") or {}
        return data.get("chats", []) if isinstance(data, dict) else data or []

    async def create_chat(self, name: str, dataset_ids: list[str]) -> str:
        """返回 chat_id（租户默认 LLM 由 onboarding/ensure 绑定）。"""
        body = await self._request(
            "POST", "/api/v1/chats", json={"name": name, "dataset_ids": dataset_ids}
        )
        data = body.get("data") or {}
        chat_id = data.get("id") or ""
        if not chat_id:
            raise RagflowError(502, f"no chat id in response: {body}")
        return chat_id

    async def set_default_chat_model(self) -> None:
        """租户默认 chat 模型 → SILICONFLOW（幂等，确保问答可用）。"""
        await self._request(
            "PATCH",
            "/api/v1/models/default",
            json={
                "model_type": "chat",
                "model_provider": "SILICONFLOW",
                "model_instance": "sf-main",
                "model_name": settings.SILICONFLOW_CHAT_MODEL,
            },
        )

    async def update_chat_datasets(self, chat_id: str, dataset_ids: list[str]) -> None:
        """同步助手库绑定（P0-0：旧助手只绑首库 → 多库检索不全）。"""
        await self._request(
            "PUT", f"/api/v1/chats/{chat_id}", json={"dataset_ids": dataset_ids}
        )

    async def ensure_chat(self, dataset_ids: list[str]) -> str:
        """找名叫 portal-assistant 的助手，没有则建；返回 chat_id。
        复用时若绑定漂移则同步为全量库（同步失败仅告警，沿用旧绑定不炸问答）。
        空库（无已解析文件）时降级为无库助手（纯 LLM 问答）。"""
        for c in await self.list_chats():
            if c.get("name") == "portal-assistant":
                bound = c.get("dataset_ids")
                if bound is None or set(bound) != set(dataset_ids):
                    try:
                        await self.update_chat_datasets(c["id"], dataset_ids)
                    except RagflowError as e:
                        _logger.warning(
                            "sync portal-assistant datasets failed, keep stale binding: %s",
                            e.message,
                        )
                return c["id"]
        try:
            await self.set_default_chat_model()
        except RagflowError:
            pass  # 已绑过则忽略
        try:
            return await self.create_chat("portal-assistant", dataset_ids)
        except RagflowError as e:
            if "doesn't own parsed file" in e.message and dataset_ids:
                return await self.create_chat("portal-assistant", [])
            raise

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

    async def stream_chat(self, chat_id: str, messages: list[dict]):
        """OpenAI 兼容流式问答（SSE 逐块产出 bytes）。
        按行缓冲重发：reference.chunks 补 document_name 后透传全字段（P0-①）。"""
        async def gen():
            async with self._client.stream(
                "POST",
                f"/api/v1/openai/{chat_id}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"model": "model", "messages": messages, "stream": True,
                      "extra_body": {"reference": True}},
            ) as resp:
                if resp.status_code >= 400:
                    text = (await resp.aread()).decode()[:300]
                    yield f'data: {{"error": "{text}"}}\n\n'.encode()
                    return
                buf = b""
                async for raw in resp.aiter_bytes():
                    buf += raw
                    *lines, buf = buf.split(b"\n")
                    for line in lines:
                        yield _reemit_sse_line(line)
                if buf:
                    yield _reemit_sse_line(buf)
        return gen()

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
        data = await self.list_chunks_page(dataset_id, document_id, page=page, page_size=page_size)
        return data.get("chunks") or []

    async def list_chunks_page(
        self, dataset_id: str, document_id: str, keywords: str = "",
        page: int = 1, page_size: int = 20,
    ) -> dict:
        """切片分页列表：返回引擎 data（chunks/total），供网关端点透传。"""
        params: dict = {"page": page, "page_size": min(page_size, 100)}
        if keywords:
            params["keywords"] = keywords
        body = await self._request(
            "GET",
            f"/api/v1/datasets/{dataset_id}/documents/{document_id}/chunks",
            params=params,
        )
        return body.get("data") or {}

    async def get_chunk(self, dataset_id: str, document_id: str, chunk_id: str) -> dict:
        body = await self._request(
            "GET",
            f"/api/v1/datasets/{dataset_id}/documents/{document_id}/chunks/{chunk_id}",
        )
        data = body.get("data") or {}
        # 某些版本把单条包在 chunks 里：统一拆出
        if isinstance(data, dict) and "id" not in data:
            inner = data.get("chunks")
            if isinstance(inner, list) and inner:
                return inner[0]
        return data

    async def update_chunk(
        self, dataset_id: str, document_id: str, chunk_id: str, fields: dict
    ) -> None:
        """切片纠错（v0.27 规范动词 PATCH；PUT 为弃用别名）。"""
        await self._request(
            "PATCH",
            f"/api/v1/datasets/{dataset_id}/documents/{document_id}/chunks/{chunk_id}",
            json=fields,
        )

    async def delete_chunks(
        self, dataset_id: str, document_id: str, chunk_ids: list[str]
    ) -> None:
        """引擎删除为批量端点（body chunk_ids）；网关单条语义在此收敛。"""
        await self._request(
            "DELETE",
            f"/api/v1/datasets/{dataset_id}/documents/{document_id}/chunks",
            json={"chunk_ids": chunk_ids},
        )

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
        self,
        question: str,
        dataset_ids: list[str],
        page_size: int = 10,
        similarity_threshold: float | None = None,
        vector_similarity_weight: float | None = None,
        rerank_id: str | None = None,
        keyword: bool | None = None,
        highlight: bool | None = None,
        metadata_condition: dict | None = None,
        document_ids: list[str] | None = None,
    ) -> dict:
        """返回 data（chunks 在 data.chunks[]，全字段由路由层映射）。
        page_size 为网关自有 top_n 截断；RAGFlow top_k/knn_top_k 已弃用，不透传。
        document_ids：服务端推导的可见白名单（#29 ACL 预过滤通道）；
        None = 不过滤（方案 A：部门内全员可见，隔离已在租户账号层）。"""
        body: dict = {"question": question, "dataset_ids": dataset_ids, "page_size": page_size}
        for k, v in {
            "similarity_threshold": similarity_threshold,
            "vector_similarity_weight": vector_similarity_weight,
            "rerank_id": rerank_id,
            "keyword": keyword,
            "highlight": highlight,
            "metadata_condition": metadata_condition,
            "document_ids": document_ids,
        }.items():
            if v is not None:
                body[k] = v
        payload = await self._request("POST", "/api/v1/retrieval", json=body)
        return payload.get("data") or {}

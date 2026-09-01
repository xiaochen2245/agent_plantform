"""Dify 服务 API 客户端（唯一出口：POST /v1/chat-messages 流式）。

- AsyncClient 生命周期归 app.main lifespan（进程级单例，连接池复用，设计 §13.1）
- transport 可注入：测试用 httpx.MockTransport / 自定义流式替身
- app key：env DIFY_API_KEY_APP_<id>，缺省 demo-key（真实 key 后续切片入库加密）
"""
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from app.core.config import settings

# 120s 总超时 / 10s 连接超时（设计 §5.2）；流式调用不重试（§7.3）
DIFY_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


def app_api_key(app_id: int) -> str:
    return os.environ.get(f"DIFY_API_KEY_APP_{app_id}", "demo-key")


class DifyClient:
    def __init__(
        self,
        base_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=(base_url or settings.DIFY_BASE_URL).rstrip("/"),
            timeout=DIFY_TIMEOUT,
            transport=transport,
            # Dify 是内网服务：不读 http(s)_proxy/ALL_PROXY 环境变量，
            # 避免 LAN 流量被代理接管（也免去 socksio 依赖）
            trust_env=False,
        )

    @asynccontextmanager
    async def stream_chat(self, app_id: int, payload: dict) -> AsyncIterator[httpx.Response]:
        """流式调用 Dify chat-messages；状态码由调用方判定。"""
        request = self._client.build_request(
            "POST",
            "/v1/chat-messages",
            json=payload,
            headers={"Authorization": f"Bearer {app_api_key(app_id)}"},
        )
        response = await self._client.send(request, stream=True)
        try:
            yield response
        finally:
            await response.aclose()

    @asynccontextmanager
    async def stream_workflow(self, app_id: int, payload: dict) -> AsyncIterator[httpx.Response]:
        """流式调用 Dify workflows/run（契约 v3 工作流模式）；状态码由调用方判定。"""
        request = self._client.build_request(
            "POST",
            "/v1/workflows/run",
            json=payload,
            headers={"Authorization": f"Bearer {app_api_key(app_id)}"},
        )
        response = await self._client.send(request, stream=True)
        try:
            yield response
        finally:
            await response.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

"""FakeDify：httpx.MockTransport 替身，产出多行 SSE 流。

用法（tests 内）：
    dify = fake_dify_client(SSE_OK)                       # 正常流
    dify = fake_dify_client(b"", status=500)              # 上游 5xx
    dify = exploding_dify_client(FIRST_CHUNK)             # 流中途断开
    app.dependency_overrides[get_dify] = lambda: dify
"""
import json
from collections.abc import AsyncIterator

import httpx

from app.dify.client import DifyClient


def sse_events(*events: tuple[str, dict]) -> bytes:
    """把 (event_name, data) 序列编码成 Dify 风格 SSE 字节流。"""
    lines: list[str] = []
    for name, data in events:
        lines.append(f"event: {name}")
        lines.append(f"data: {json.dumps(data, ensure_ascii=False)}")
        lines.append("")
    return ("\n".join(lines) + "\n").encode()


# 正常流：3 段增量 + message_end（带 usage 与 dify conversation_id）
SSE_OK = sse_events(
    ("message", {"answer": "你"}),
    ("message", {"answer": "好"}),
    ("message", {"answer": "！"}),
    (
        "message_end",
        {
            "metadata": {"usage": {"prompt": 10, "completion": 20, "total": 30}},
            "conversation_id": "dify-conv-abc",
        },
    ),
)


def fake_dify_client(body: bytes, status: int = 200, captured: list | None = None) -> DifyClient:
    """固定响应体（可整段捕获请求供断言）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(
                {
                    "url": str(request.url),
                    "headers": dict(request.headers),
                    "json": json.loads(request.content.decode()),
                }
            )
        return httpx.Response(
            status,
            content=body,
            headers={"content-type": "text/event-stream"},
        )

    return DifyClient(base_url="http://fake-dify", transport=httpx.MockTransport(handler))


def exploding_dify_client(first_chunk: bytes) -> DifyClient:
    """读到第二段就抛异常 —— 模拟 Dify 连接中途断开。"""

    async def stream() -> AsyncIterator[bytes]:
        yield first_chunk
        raise RuntimeError("connection reset mid-stream")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=stream())

    return DifyClient(base_url="http://fake-dify", transport=httpx.MockTransport(handler))

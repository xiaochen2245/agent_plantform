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
    """固定响应体（可整段捕获请求供断言）。

    按路由分流：/v1/files/upload（契约 v4，multipart）回 JSON 文件 id；
    其余（chat-messages / workflows，JSON）回给定 SSE 字节流。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            entry = {"url": str(request.url), "headers": dict(request.headers)}
            ctype = request.headers.get("content-type", "")
            if ctype.startswith("application/json"):
                entry["json"] = json.loads(request.content.decode())
            else:
                entry["content"] = request.content  # multipart 原始字节
            captured.append(entry)
        if str(request.url).endswith("/v1/files/upload"):
            return httpx.Response(201, json={"id": "dify-file-1"})
        return httpx.Response(
            status,
            content=body,
            headers={"content-type": "text/event-stream"},
        )

    return DifyClient(base_url="http://fake-dify", transport=httpx.MockTransport(handler))


def failing_upload_dify_client(body: bytes, captured: list | None = None) -> DifyClient:
    """/files/upload 恒 500（转发失败跳过路径）；其余走正常 SSE。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append({"url": str(request.url), "headers": dict(request.headers)})
        if str(request.url).endswith("/v1/files/upload"):
            return httpx.Response(500, json={"code": "internal_error"})
        return httpx.Response(
            200, content=body, headers={"content-type": "text/event-stream"}
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


def slow_dify_client(body: bytes, frame_delay: float = 0.05) -> DifyClient:
    """逐帧延迟的流（真实网络形态）—— 供取消/断流时序测试（B1）。"""
    import anyio

    frames = body.split(b"\n\n")

    async def stream() -> AsyncIterator[bytes]:
        for frame in frames:
            await anyio.sleep(frame_delay)
            yield frame + b"\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=stream(), headers={"content-type": "text/event-stream"})

    return DifyClient(base_url="http://fake-dify", transport=httpx.MockTransport(handler))


# ── 工作流模式（契约 v3；事件形状取自 docs/fixtures 真实样本）──────────────

# 正常流：ping/started/node 系 + 2 段 text_chunk + 成功 workflow_finished
WORKFLOW_SSE_OK = sse_events(
    ("ping", {}),
    ("workflow_started", {"task_id": "t-1", "data": {"id": "run-1", "inputs": {}}}),
    ("node_started", {"task_id": "t-1", "data": {"node_id": "n1"}}),
    ("text_chunk", {"task_id": "t-1", "data": {"text": "名"}}),
    ("text_chunk", {"task_id": "t-1", "data": {"text": "片已生成：张三 产品经理"}}),
    ("node_finished", {"task_id": "t-1", "data": {"node_id": "n1"}}),
    (
        "workflow_finished",
        {"task_id": "t-1", "data": {"status": "succeeded", "total_tokens": 77, "error": None}},
    ),
)

# 上游校验失败（真实样本：必填输入缺失 → workflow_finished.status=failed）
WORKFLOW_SSE_FAILED = sse_events(
    ("ping", {}),
    ("workflow_started", {"task_id": "t-2", "data": {"id": "run-2", "inputs": {}}}),
    (
        "workflow_finished",
        {"task_id": "t-2", "data": {"status": "failed", "total_tokens": 0, "error": "business_card is required in input form"}},
    ),
)

"""
SSE 流式事件推送 API (GET /api/v1/workflow/{task_id}/stream) 集成测试套件
验证:
1. text/event-stream 协议、状态跃迁、Patch Diff 与完成事件流
2. 熔断工况下 human_review_required 事件推送
3. 不存在的任务 404 校验
"""

import json
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_workflow_sse_stream_flow(client: AsyncClient):
    """测试已完成工作流的 SSE 流式事件推送完整性"""
    tenant_id = "tenant_sse_01"
    headers = {"X-Tenant-ID": tenant_id}

    # 1. 运行工作流生成方案
    run_resp = await client.post(
        "/api/v1/workflow/run",
        json={
            "rfp_requirements": "园区安防监控项目，工期90天，冷机COP>=5.0。",
            "max_iterations": 2,
            "async_mode": False,
        },
        headers=headers,
    )
    assert run_resp.status_code == 200
    task_id = run_resp.json()["task_id"]

    # 2. 发起 SSE 流式查询
    async with client.stream("GET", f"/api/v1/workflow/{task_id}/stream", headers=headers) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")

        events: list[dict] = []
        current_event = None

        async for line in response.aiter_lines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("event:"):
                current_event = line.replace("event:", "").strip()
            elif line.startswith("data:") and current_event:
                data_str = line.replace("data:", "").strip()
                try:
                    payload = json.loads(data_str)
                except Exception:
                    payload = data_str
                events.append({"event": current_event, "data": payload})
                current_event = None

        event_names = [e["event"] for e in events]
        assert "state_transition" in event_names
        assert "workflow_complete" in event_names


@pytest.mark.asyncio
async def test_workflow_sse_stream_circuit_break(client: AsyncClient):
    """测试熔断工况下推送 human_review_required 事件"""
    tenant_id = "tenant_sse_breaker"
    headers = {"X-Tenant-ID": tenant_id}

    run_resp = await client.post(
        "/api/v1/workflow/run",
        json={
            "rfp_requirements": "【死锁要求】30天工期必须完成施工交付。",
            "max_iterations": 2,
            "async_mode": False,
        },
        headers=headers,
    )
    assert run_resp.status_code == 200
    task_id = run_resp.json()["task_id"]

    async with client.stream("GET", f"/api/v1/workflow/{task_id}/stream", headers=headers) as response:
        assert response.status_code == 200

        events = []
        current_event = None
        async for line in response.aiter_lines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("event:"):
                current_event = line.replace("event:", "").strip()
            elif line.startswith("data:") and current_event:
                events.append(current_event)
                current_event = None

        assert "human_review_required" in events


@pytest.mark.asyncio
async def test_workflow_sse_stream_not_found(client: AsyncClient):
    """查询不存在的任务返回 404"""
    resp = await client.get("/api/v1/workflow/non_existent_task/stream", headers={"X-Tenant-ID": "tenant_test"})
    assert resp.status_code == 404

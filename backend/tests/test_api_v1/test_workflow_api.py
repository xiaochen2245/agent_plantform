"""
双智能体工作流 API (POST /workflow/run, GET /state, POST /resume) 集成测试套件
验证:
1. 同步闭环反思并审核通过 (Approved)
2. 连续负偏离触发 2 次迭代硬熔断至 human_review
3. 状态快照与审计历史追踪查询 (GET /state)
4. HITL 人工干预特批恢复至 SUCCESS
5. HITL 人工驳回至 FAILED
6. 异步 Celery 调度模式 (async_mode=True)
"""

import pytest
from httpx import AsyncClient

from app.models.audit_rag import TaskStatus


@pytest.mark.asyncio
async def test_workflow_run_sync_approved(client: AsyncClient):
    """测试合规参数下双智能体方案生成与校核，反思修正后成功通过"""
    headers = {"X-Tenant-ID": "tenant_wf_01"}
    payload = {
        "rfp_requirements": "本工程为智能化园区建设，要求总工期必须控制在 90 个日历天之内，机房精密空调及冷水机组能效比 COP 必须达到 5.0 以上。",
        "max_iterations": 2,
        "async_mode": False,
    }

    resp = await client.post("/api/v1/workflow/run", json=payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    assert data["status"] == TaskStatus.SUCCESS.value
    assert data["is_async"] is False
    assert data["draft"] is not None
    assert len(data["draft"]) > 0
    assert data["iteration_count"] >= 1
    assert data["audit_feedback"] is not None
    assert data["audit_feedback"]["passed"] is True


@pytest.mark.asyncio
async def test_workflow_run_sync_circuit_breaker(client: AsyncClient):
    """测试不可调和工期矛盾（30天极限工期），验证 2 次反思后坚固熔断至 human_review"""
    headers = {"X-Tenant-ID": "tenant_wf_breaker"}
    payload = {
        "rfp_requirements": "【绝密死锁要求】招标文件要求主体智能化必须在 30 个日历天内竣工交付，严禁违背！同时要求冷机COP达到8.0极值。",
        "max_iterations": 2,
        "async_mode": False,
    }

    resp = await client.post("/api/v1/workflow/run", json=payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    # 验证触发熔断挂起在 human_review
    assert data["status"] == TaskStatus.HUMAN_REVIEW.value
    assert data["iteration_count"] == 2
    task_id = data["task_id"]

    # 查询状态快照
    state_resp = await client.get(f"/api/v1/workflow/{task_id}/state", headers=headers)
    assert state_resp.status_code == 200
    sdata = state_resp.json()
    assert sdata["status"] == TaskStatus.HUMAN_REVIEW.value
    assert len(sdata["review_history"]) >= 2

    # 人工干预特批恢复
    resume_resp = await client.post(
        f"/api/v1/workflow/{task_id}/resume",
        json={
            "decision": "override_and_finish",
            "human_patch": "【专家特别批准方案】经总工程师委员会论证，主体施工工期特批调整为合理合规的 90 个日历天，冷机 COP 确认为 5.4，批准准入施工！",
            "comments": "专家特批通过",
        },
        headers=headers,
    )
    assert resume_resp.status_code == 200
    rdata = resume_resp.json()
    assert rdata["status"] == TaskStatus.SUCCESS.value
    assert "专家特别批准方案" in rdata["final_draft"]


@pytest.mark.asyncio
async def test_workflow_hitl_resume_reject(client: AsyncClient):
    """测试人工驳回，验证状态流转至 FAILED"""
    headers = {"X-Tenant-ID": "tenant_wf_reject"}
    payload = {
        "rfp_requirements": "【死锁要求】30天工期必须完成全部施工交付。",
        "max_iterations": 2,
        "async_mode": False,
    }

    resp = await client.post("/api/v1/workflow/run", json=payload, headers=headers)
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]

    # 人工驳回
    resume_resp = await client.post(
        f"/api/v1/workflow/{task_id}/resume",
        json={"decision": "reject", "comments": "重大安全隐患，无法特批，驳回投标"},
        headers=headers,
    )
    assert resume_resp.status_code == 200
    assert resume_resp.json()["status"] == TaskStatus.FAILED.value


@pytest.mark.asyncio
async def test_workflow_run_async_celery_dispatch(client: AsyncClient):
    """测试 Celery 异步调度执行模式"""
    headers = {"X-Tenant-ID": "tenant_wf_async"}
    payload = {
        "rfp_requirements": "园区安防弱电智能化项目，工期90天，要求冷机COP>=5.0。",
        "max_iterations": 2,
        "async_mode": True,
    }

    resp = await client.post("/api/v1/workflow/run", json=payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_async"] is True
    assert "task_id" in data

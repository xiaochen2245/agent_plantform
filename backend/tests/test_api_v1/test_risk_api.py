"""
历史工程经验与前置风险拦截 API (POST /risk/intercept, POST /risk/seed, GET /risk/list) 集成测试套件
验证:
1. 超危大深基坑 (开挖深度 7.5m) 强触发住建部 37 号令专家论证预警与护栏生成
2. 雨季紧迫工期预警
3. 风险库多租户数据隔离
"""

import pytest
from httpx import AsyncClient

from app.models.audit_rag import SeverityLevel


@pytest.mark.asyncio
async def test_risk_intercept_deep_excavation(client: AsyncClient):
    """测试开挖深度 7.5m 立项触发 CRITICAL 级深基坑坍塌预警并生成 37 号令护栏"""
    tenant_id = "tenant_risk_pit"
    headers = {"X-Tenant-ID": tenant_id}

    # 1. 播种案例
    seed_resp = await client.post("/api/v1/risk/seed", headers=headers)
    assert seed_resp.status_code == 201
    assert seed_resp.json()["seeded_count"] == 5

    # 2. 发起拦截诊断
    charter_payload = {
        "project_name": "滨海商务中心超高层综合体",
        "project_type": "房建",
        "scale_description": "总建筑面积 15 万平方米，地下 3 层，基坑开挖深度达 7.5 米，富水软土地层",
        "duration_days": 720,
        "budget_cny_ten_thousand": 48000.0,
        "excavation_depth_meters": 7.5,
        "special_conditions": ["深基坑", "富水地层", "危大工程"],
        "charter_text": "项目位于沿海滩涂沉积软土区域，紧邻城市主干道与地铁线，基坑最深处开挖深度为 7.5m。",
    }

    resp = await client.post("/api/v1/risk/intercept", json=charter_payload, headers=headers)
    assert resp.status_code == 200
    report = resp.json()

    assert report["risk_level"] == SeverityLevel.CRITICAL.value
    assert report["critical_count"] >= 1
    assert len(report["warnings"]) > 0

    # 验证生成的预防护栏提示词片段
    guardrail = report["guardrail_system_prompt_snippet"]
    assert "深基坑" in guardrail
    assert "专家论证" in guardrail or "37 号令" in guardrail or "37号令" in guardrail


@pytest.mark.asyncio
async def test_risk_intercept_duration_tight(client: AsyncClient):
    """测试紧迫工期与雨季工况预警"""
    tenant_id = "tenant_risk_sched"
    headers = {"X-Tenant-ID": tenant_id}

    await client.post("/api/v1/risk/seed", headers=headers)

    charter_payload = {
        "project_name": "市民广场地下管廊市政工程",
        "project_type": "市政",
        "scale_description": "全长 3.2 公里地下综合管廊",
        "duration_days": 120,
        "special_conditions": ["雨季施工", "管线复杂"],
    }

    resp = await client.post("/api/v1/risk/intercept", json=charter_payload, headers=headers)
    assert resp.status_code == 200
    report = resp.json()
    assert report["total_risks_matched"] > 0


@pytest.mark.asyncio
async def test_risk_tenant_isolation(client: AsyncClient):
    """多租户隔离验证：租户 Alpha 播种的案例，租户 Beta 列表中不可见"""
    # 租户 Alpha 播种
    await client.post("/api/v1/risk/seed", headers={"X-Tenant-ID": "tenant_risk_alpha"})

    # 租户 Beta 查询列表
    resp_beta = await client.get("/api/v1/risk/list", headers={"X-Tenant-ID": "tenant_risk_beta"})
    assert resp_beta.status_code == 200
    data_beta = resp_beta.json()
    assert data_beta["total"] == 0
    assert len(data_beta["items"]) == 0

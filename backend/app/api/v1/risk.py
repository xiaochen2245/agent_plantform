"""
历史工程经验知识库与主动风险拦截 API 路由 (Risk API)
提供:
1. POST /api/v1/risk/intercept: 项目立项前置主动风险拦截与生成防护栏提示词
2. POST /api/v1/risk/seed: 为当前租户初始化注入 5 大基准工程案例
3. GET /api/v1/risk/list: 查询当前租户历史经验风险知识库条目
"""

from typing import List
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_tenant_db, get_tenant_id
from app.models.audit_rag import HistoricalAuditRisk, Tenant
from app.schemas.gateway import RiskListResponse, RiskSeedResponse
from app.workflow.contracts import (
    HistoricalAuditRiskResponse,
    ProjectCharter,
    RiskInterceptionReport,
)
from app.workflow.risk_warning import ProjectRiskInterceptor, seed_historical_risks

router = APIRouter()


async def _ensure_tenant(session: AsyncSession, tenant_id: str) -> None:
    tenant = await session.get(Tenant, tenant_id)
    if not tenant:
        tenant = Tenant(id=tenant_id, code=tenant_id, name=f"Enterprise {tenant_id}")
        session.add(tenant)
        await session.flush()


@router.post("/intercept", response_model=RiskInterceptionReport)
async def intercept_project_risks(
    charter: ProjectCharter,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
    top_k: int = Query(default=5, ge=1, le=20),
) -> RiskInterceptionReport:
    """
    接收工程立项任务书 (ProjectCharter)，基于规则触发器与历史案例语义召回执行前置风险拦截，
    输出高管风险摘要并生成注入 Generator Agent 的系统预防护栏提示词。
    """
    await _ensure_tenant(db, tenant_id)
    interceptor = ProjectRiskInterceptor()
    report = await interceptor.intercept_project_risks(
        session=db,
        tenant_id=tenant_id,
        charter=charter,
        top_k=top_k,
    )
    return report


@router.post("/seed", response_model=RiskSeedResponse, status_code=status.HTTP_201_CREATED)
async def seed_tenant_historical_risks(
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
) -> RiskSeedResponse:
    """
    为当前租户快速播种 5 大经典工程领域（深基坑开挖坍塌、雨季工期延误、造价超概、人员资质、泥浆环保）基准历史风险案卷。
    """
    await _ensure_tenant(db, tenant_id)
    seeded_objs = await seed_historical_risks(session=db, tenant_id=tenant_id)
    return RiskSeedResponse(
        tenant_id=tenant_id,
        seeded_count=len(seeded_objs),
        message=f"已成功为租户 '{tenant_id}' 播种并向量化 {len(seeded_objs)} 条基准历史工程风险案卷",
    )


@router.get("/list", response_model=RiskListResponse)
async def list_historical_risks(
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> RiskListResponse:
    """
    分页查询当前租户拥有的历史工程经验风险知识库条目。
    严格受多租户 RLS 与应用层租户隔离约束。
    """
    count_stmt = select(func.count(HistoricalAuditRisk.id)).where(HistoricalAuditRisk.tenant_id == tenant_id)
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = (
        select(HistoricalAuditRisk)
        .where(HistoricalAuditRisk.tenant_id == tenant_id)
        .order_by(HistoricalAuditRisk.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    records = (await db.execute(stmt)).scalars().all()

    items: List[HistoricalAuditRiskResponse] = []
    for r in records:
        items.append(
            HistoricalAuditRiskResponse(
                id=r.id,
                tenant_id=r.tenant_id,
                project_type=r.project_type,
                risk_category=r.risk_category,
                risk_title=r.risk_title,
                severity=r.severity,
                defect_description=r.defect_description,
                lesson_learned=r.lesson_learned,
                preventive_guardrail_prompt=r.preventive_guardrail_prompt,
                tags=r.tags or [],
                rule_conditions=r.rule_conditions or {},
                source_case_id=r.source_case_id,
                source_project_name=r.source_project_name,
                financial_loss_cny=r.financial_loss_cny,
                delay_days=r.delay_days,
                has_embedding=bool(r.embedding is not None),
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
        )

    return RiskListResponse(total=total, items=items)

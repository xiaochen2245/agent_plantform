"""
单元测试: 历史工程审查经验知识库与主动风险拦截引擎 (Features 29 & 30)
验证:
1. HistoricalAuditRisk 模型字段与多租户隔离
2. 5 大基准种子案例播种与多通道混合召回
3. 开挖深度 >= 5m 住建部危大工程规则加权与置信度校准
4. ProactiveProjectRiskInterceptor 报告输出与预防护栏提示词装配
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models.audit_rag import HistoricalAuditRisk, SeverityLevel, Tenant
from app.rag.embedding import get_embedding_service
from app.workflow.contracts import ProjectCharter, RiskInterceptionReport
from app.workflow.risk_warning import (
    SEED_HISTORICAL_RISKS,
    HistoricalRiskSearchEngine,
    ProjectRiskInterceptor,
    seed_historical_risks,
)


@pytest.fixture
async def workflow_db_session():
    """初始化用于测试知识库检索的干净 SQLite 数据库"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        t_a = Tenant(id="tenant_construction_a", code="T_A", name="中建某局")
        t_b = Tenant(id="tenant_construction_b", code="T_B", name="中铁某局")
        session.add_all([t_a, t_b])
        await session.commit()
        yield session


def test_historical_audit_risk_model_fields():
    """验证 HistoricalAuditRisk 实体字段完整性与数据表映射"""
    assert HistoricalAuditRisk.__tablename__ == "historical_audit_risks"
    expected_attrs = [
        "id",
        "tenant_id",
        "project_type",
        "risk_category",
        "risk_title",
        "severity",
        "defect_description",
        "lesson_learned",
        "preventive_guardrail_prompt",
        "tags",
        "rule_conditions",
        "source_case_id",
        "source_project_name",
        "financial_loss_cny",
        "delay_days",
        "embedding",
        "created_at",
        "updated_at",
    ]
    for attr in expected_attrs:
        assert hasattr(HistoricalAuditRisk, attr), f"HistoricalAuditRisk 缺少必要字段: {attr}"


@pytest.mark.asyncio
async def test_seed_historical_risks_and_tenant_isolation(workflow_db_session: AsyncSession):
    """验证种子风险案例播种与多租户物理隔离 (租户 A 播种后租户 B 零可见)"""
    session = workflow_db_session
    t_a = "tenant_construction_a"
    t_b = "tenant_construction_b"

    # 为租户 A 播种 5 条基准案例
    seeded_a = await seed_historical_risks(session, tenant_id=t_a)
    assert len(seeded_a) == 5

    # 验证租户 A 查询结果为 5
    res_a = await session.execute(
        select(HistoricalAuditRisk).where(HistoricalAuditRisk.tenant_id == t_a)
    )
    assert len(res_a.scalars().all()) == 5

    # 验证租户 B 零数据泄露 (严格多租户隔离)
    res_b = await session.execute(
        select(HistoricalAuditRisk).where(HistoricalAuditRisk.tenant_id == t_b)
    )
    assert len(res_b.scalars().all()) == 0


@pytest.mark.asyncio
async def test_excavation_depth_rule_trigger(workflow_db_session: AsyncSession):
    """
    验证开挖深度 >= 5.0m 硬约束规则加权:
    输入 6.5m 基坑开挖，精确触发基坑坍塌风险并激活住建部 37 号令专家论证红线
    """
    session = workflow_db_session
    tenant_id = "tenant_construction_a"
    await seed_historical_risks(session, tenant_id=tenant_id)

    search_engine = HistoricalRiskSearchEngine()
    charter = ProjectCharter(
        project_name="高新商务中心二期",
        project_type="房建",
        scale_description="总建面10万㎡，地下3层",
        excavation_depth_meters=6.5,  # >= 5.0m 超危大工程
        special_conditions=["深基坑", "富水地层"],
    )

    matches = await search_engine.search_matched_risks(
        session=session, tenant_id=tenant_id, charter=charter, top_k=3
    )

    assert len(matches) >= 1
    top_risk, confidence, reasons = matches[0]

    # 验证命中深基坑安全坍塌风险
    assert "基坑" in top_risk.risk_title
    assert top_risk.severity == SeverityLevel.CRITICAL
    assert confidence >= 0.70

    # 验证匹配理由包含住建部 37 号令专项专家论证
    reasons_str = " ".join(reasons)
    assert "6.5m >= 5.0m" in reasons_str
    assert "住建部 37 号令" in reasons_str


@pytest.mark.asyncio
async def test_proactive_interceptor_report_output(workflow_db_session: AsyncSession):
    """验证 ProactiveProjectRiskInterceptor 输出结构化预警报告与预防护栏提示词"""
    session = workflow_db_session
    tenant_id = "tenant_construction_a"
    await seed_historical_risks(session, tenant_id=tenant_id)

    interceptor = ProjectRiskInterceptor()
    charter = ProjectCharter(
        project_name="市重点三甲医院弱电智能化工程",
        project_type="弱电智能化",
        scale_description="包含医疗专用网络、手术室控制与洁净病房",
        budget_cny_ten_thousand=1500.0,
        special_conditions=["医疗专用网络", "暂估价", "三甲医院"],
    )

    report: RiskInterceptionReport = await interceptor.intercept_project_risks(
        session=session, tenant_id=tenant_id, charter=charter, top_k=3
    )

    assert report.project_name == charter.project_name
    assert report.total_risks_matched >= 1
    assert len(report.warnings) >= 1

    # 验证三甲医院造价超概风险命中
    cost_warn = next((w for w in report.warnings if "造价超概" in w.risk_category or "超概算" in w.risk_title), None)
    assert cost_warn is not None
    assert "PRJ-2024-YL-008" in cost_warn.historical_case_reference.get("case_id", "")

    # 验证生成的系统提示词护栏片段有效
    snippet = report.guardrail_system_prompt_snippet
    assert "历史工程事故与审计风险强制预防护栏" in snippet
    assert "技术界面协议" in snippet or "暂估价" in snippet

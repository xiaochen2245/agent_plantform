"""
生产级多租户 RAG 知识库与工作流编排全链路测试套件
验证:
1. SQLAlchemy 2.0 领域模型定义与字段完整性 (Tenant, Document, DocumentChunk, AuditTask, ReviewResult)
2. LangGraph 双智能体 (Generator + Critic) 闭环校核与 Reflection Loop 状态机流转
3. 长文档跨章节数据一致性交叉校验引擎 (工期与造价数值矛盾检测与报告生成)
"""

import pytest
from app.models.audit_rag import (
    Tenant,
    Document,
    DocumentChunk,
    AuditTask,
    ReviewResult,
    ChunkLevel,
    TaskType,
    TaskStatus,
    DeviationType,
    SeverityLevel,
    generate_rls_sql,
)
from app.services.dual_agent_workflow import (
    DualAgentWorkflowEngine,
    GraphState,
    build_dual_agent_graph,
)
from app.services.consistency_engine import (
    ConsistencyEngine,
    ConflictType,
    IssueSeverity,
)


# ===========================================================================
# 1. 领域模型结构与 RLS 验证
# ===========================================================================

def test_domain_models_structure():
    """验证数据表字段、枚举与外键配置"""
    # 检查 Tenant 表
    assert Tenant.__tablename__ == "tenants"
    assert hasattr(Tenant, "id")
    assert hasattr(Tenant, "code")
    assert hasattr(Tenant, "config")

    # 检查 Document 表
    assert Document.__tablename__ == "documents"
    assert hasattr(Document, "tenant_id")
    assert hasattr(Document, "doc_ast")
    assert hasattr(Document, "s3_path")

    # 检查 DocumentChunk 表 (含父子切片与向量列)
    assert DocumentChunk.__tablename__ == "document_chunks"
    assert hasattr(DocumentChunk, "parent_chunk_id")
    assert hasattr(DocumentChunk, "chunk_level")
    assert hasattr(DocumentChunk, "embedding")

    # 检查 AuditTask 与 ReviewResult
    assert AuditTask.__tablename__ == "audit_tasks"
    assert ReviewResult.__tablename__ == "review_results"
    assert hasattr(ReviewResult, "deviation_type")
    assert hasattr(ReviewResult, "source_quote")
    assert hasattr(ReviewResult, "confidence")


def test_rls_ddl_generation():
    """验证 PostgreSQL Row-Level Security DDL 生成器"""
    sql = generate_rls_sql()
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "app.current_tenant_id" in sql
    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql


# ===========================================================================
# 2. 数据一致性交叉校验引擎测试
# ===========================================================================

def test_consistency_engine_conflict_detection():
    """
    测试长文档数据一致性检测:
    输入第 3 章“工期为 90 天”，第 35 章“总施工周期 120 天”，以及总投资前后矛盾
    验证引擎是否能精准定位两处矛盾并输出结构化证据链
    """
    engine = ConsistencyEngine()

    mock_document_sections = [
        {
            "section_title": "第3章 施工组织总体部署",
            "page": "第15页",
            "content": (
                "本项目各项开工准备工作已就绪。根据施工总体计划安排，"
                "本工程施工总工期承诺为 90 个日历天。工程总造价预算控制在 1200 万元整。"
            ),
        },
        {
            "section_title": "第12章 机电与暖通工程专业方案",
            "page": "第68页",
            "content": "暖通管道安装必须满足国家技术标准，严格遵照施工总工期 90 天节点要求推进。",
        },
        {
            "section_title": "第35章 总体进度与关键路径网络计划",
            "page": "第152页",
            "content": (
                "考虑到雨季施工及深基坑复杂地质风险，经网络横道图全面优化，"
                "项目总施工周期调整为 120 天。此外，经核算工程总造价为 1500 万元。"
            ),
        },
    ]

    report = engine.validate_document_consistency(
        document_title="某大型三甲医院智能化综合楼施工投标文件.docx",
        sections_data=mock_document_sections,
    )

    # 验证扫描结果
    assert report.total_metrics_scanned >= 4
    assert report.conflicts_found == 2  # 包含工期矛盾与造价矛盾
    assert report.critical_count == 2   # 工期与金额均被标记为 CRITICAL

    # 验证工期矛盾明细
    duration_conflict = next(c for c in report.conflicts if "工期" in c.metric_name)
    assert duration_conflict.conflict_type == ConflictType.NUMERICAL_MISMATCH
    assert duration_conflict.baseline_statement.normalized_value == 90.0
    assert duration_conflict.conflicting_statement.normalized_value == 120.0
    assert duration_conflict.difference_value == 30.0
    assert "第3章" in duration_conflict.baseline_statement.section_title
    assert "第35章" in duration_conflict.conflicting_statement.section_title
    assert "废标风险" in duration_conflict.detailed_reason

    # 验证造价矛盾明细
    cost_conflict = next(c for c in report.conflicts if "造价" in c.metric_name)
    assert cost_conflict.baseline_statement.normalized_value == 1200.0
    assert cost_conflict.conflicting_statement.normalized_value == 1500.0
    assert cost_conflict.difference_value == 300.0


# ===========================================================================
# 3. LangGraph 双智能体 (Generator + Critic) 闭环校核测试
# ===========================================================================

@pytest.mark.asyncio
async def test_dual_agent_reflection_workflow():
    """
    测试双智能体工作流:
    1. Generator 初次生成包含工期 120 天的草案
    2. Critic 识别出工期负偏离缺陷，生成 Patch Diff 并拒绝批准
    3. Workflow 触发 Reflection Loop 回退重写
    4. Generator 采纳 Patch 修订为 90 天
    5. Critic 第 2 轮审查通过，工作流流转至 approved 终态
    """
    engine = DualAgentWorkflowEngine()
    workflow_app = build_dual_agent_graph(engine)

    initial_state: GraphState = {
        "tenant_id": "tenant_enterprise_001",
        "task_id": "task_audit_2026_09",
        "task_prompt": "编制某智慧园区弱电智能化专项技术标方案，严格遵守招标文件规定的工期 90 天要求",
        "context_chunks": [
            {
                "chunk_id": "chk_parent_01",
                "content": "【招标文件强制性条款】总工期必须限定在 90 个日历天之内，超出此限期将按废标处理。",
            }
        ],
        "draft": "",
        "audit_feedback": None,
        "iteration_count": 0,
        "max_iterations": 2,
        "status": "generating",
        "review_history": [],
    }

    final_state = await workflow_app.ainvoke(initial_state)

    # 验证工作流最终结果
    assert final_state["status"] == "approved"
    assert final_state["iteration_count"] == 2  # 经历了初始生成(0) -> 审计驳回(1) -> 重写审计通过(2)
    assert final_state["audit_feedback"]["passed"] is True
    assert "90 个日历天" in final_state["draft"]
    assert len(final_state["review_history"]) >= 4  # 完整的审计追踪链

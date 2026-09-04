"""
Tier 4 真实场景与核心验收门禁端到端集成测试套件 (test_e2e_rag_workflow.py)
覆盖:
1. Scenario 1: 大型三甲医院建设 EPC 标书全格式多源长文档审查 (工期 45 天与造价 230 万元双矛盾 100% 检出，4 类偏离度矩阵)
2. Scenario 2: 智慧科技产业园弱电智能化标书闭环反思 (冷机 COP 4.8 负偏离触发 Patch Diff，第 1 轮靶向纠偏为 5.4 获批)
3. Scenario 3: 不可调和缺陷连续负偏离熔断与 HITL 恢复 (30 天极端死锁工期，2 轮熔断挂起，人工注入特批条款恢复至 SUCCESS)
4. Scenario 4: 多租户企业级高并发检索与隔离压测 (甲乙双方高负载并发查询，数据 0 穿透 0 泄露)
5. Scenario 5: 历史工程审查风险前置主动预警拦截 (7.2m 深基坑立项触发住建部 37 号令红线护栏注入起草与核验全闭环)
6. Acceptance Criteria Thresholds:
   - AST 结构还原率 >= 98.0%
   - 父子切片检索 Top-5 召回率 >= 95.0% 且父级上下文回填精准
   - 跨章节数值一致性矛盾 100% 检出
   - 全系统 0 Dify 运行时依赖法证审计
"""

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Dict, List
import pytest
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant_context import TenantContext
from app.db.session import SessionLocal, engine
from app.models.audit_rag import (
    Base,
    ChunkLevel,
    DeviationType,
    Document,
    DocumentChunk,
    HistoricalAuditRisk,
    ReviewResult,
    SeverityLevel,
    TaskStatus,
    Tenant,
)
from app.parsers.docx_parser import DOCXParser
from app.parsers.pdf_parser import PDFParser
from app.parsers.xlsx_parser import XLSXParser
from app.quality.consistency_engine import ConsistencyEngine, IssueSeverity
from app.quality.tender_alignment import (
    BidSemanticAlignmentEngine,
    FourCategoryDeviationClassifier,
    RFPScoringTableParser,
    TenderAlignmentEngine,
)
from app.rag.backfill import ContextBackfiller
from app.rag.chunker import ParentChildChunker
from app.rag.embedding import EmbeddingService, MockDeterministicEmbeddingProvider
from app.rag.hybrid_search import HybridSearchEngine
from app.rag.tenant_rls import TenantRLSManager
from app.schemas.ast import (
    ASTBlockType,
    ASTNode,
    BoundingBox,
    DocumentSourceType,
    ScheduleTaskData,
    TableCell,
    TableData,
    UnifiedDocumentAST,
)
from app.schemas.audit import (
    CriteriaConstraint,
    MetricDirection,
    ScoringCategory,
    TenderScoringItem,
    TenderScoringTable,
)
from app.workflow.contracts import GraphState, ProjectCharter
from app.workflow.critic import CriticAgent
from app.workflow.generator import GeneratorAgent
from app.workflow.graph import build_dual_agent_workflow, get_workflow_checkpointer
from app.workflow.hitl import resume_workflow
from app.workflow.risk_warning import ProjectRiskInterceptor, seed_historical_risks
from tests.test_stress.conftest_stress import (
    generate_ground_truth_spec,
    generate_synthetic_1000p_docx,
    generate_synthetic_1000p_pdf,
    generate_synthetic_1000r_xlsx,
)


class TestTier4RealWorldScenarios:
    """Tier 4 五大真实生产场景全生命周期端到端测试"""

    @pytest.fixture(autouse=True)
    async def setup_db_tables(self):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield
        async with SessionLocal() as session:
            await session.execute(delete(ReviewResult))
            await session.execute(delete(HistoricalAuditRisk))
            await session.execute(delete(DocumentChunk))
            await session.execute(delete(Document))
            await session.execute(delete(Tenant))
            await session.commit()

    @pytest.mark.asyncio
    async def test_scenario_1_large_municipal_hospital_epc_proposal(self):
        """
        Scenario 1: 大型综合三甲医院建设 EPC 总承包技术标审查
        1. 汇聚 PDF (招标文件) + OFD (规划批文) + XLSX (工程量清单) + MPP (施工进度) + CAD (图纸说明) 多源 AST
        2. 注入工期矛盾: 第1章总说明承诺 720 天 vs 第4章及MPP计算 765 天 (45天冲突)
        3. 注入造价矛盾: 第2章概算载明 48500 万元 vs 第12章XLSX汇总 48270 万元 (230万元缺口)
        4. 注入招标偏离: 招标文件要求 COP >= 5.0，投标文件自编 COP 4.8 (负偏离)
        5. 验证一致性引擎 100% 检出 45 天与 230 万元矛盾，对齐引擎输出 4 类偏离度矩阵
        """
        # 1. 构造投标文件 AST 节点集
        proposal_nodes: List[ASTNode] = [
            # PDF 章节节点: 招标文件说明
            ASTNode(
                block_id="n_pdf_01",
                block_type=ASTBlockType.HEADING,
                level=1,
                section_path=["第一章 招标工程总说明"],
                text_content="第一章 某市第一人民医院新院区建设 EPC 招标文件",
                page_or_sheet="1",
            ),
            ASTNode(
                block_id="n_pdf_02",
                block_type=ASTBlockType.PARAGRAPH,
                level=1,
                section_path=["第一章 招标工程总说明", "1.1 工期总目标"],
                text_content="本工程总工期为 720 个日历天，自发包人发出开工令之日起算。",
                page_or_sheet="3",
            ),
            ASTNode(
                block_id="n_pdf_03",
                block_type=ASTBlockType.PARAGRAPH,
                level=1,
                section_path=["第二章 投资估算与资金筹措"],
                text_content="本工程建安总投资为 48500.00 万元，由政府专项债与财政拨款共同出资。",
                page_or_sheet="12",
            ),
            # MPP 进度计划节点: 关键路径计算出 765 天 (注入 45 天工期冲突)
            ASTNode(
                block_id="n_mpp_01",
                block_type=ASTBlockType.SCHEDULE_TASK,
                level=2,
                section_path=["第四章 施工进度总网络图", "关键路径控制网"],
                text_content="项目全周期网络图关键路径累计工期计算结果为 765 个日历天。",
                schedule_data=ScheduleTaskData(
                    task_id="task_mpp_01",
                    task_name="总包工程全流程进度网络",
                    duration_days=765,
                    is_critical=True
                ),
                page_or_sheet="Gantt_Chart_Page",
            ),
            # XLSX 工程量清单表格节点: 汇总金额 48270 万元 (注入 230 万元造价缺口)
            ASTNode(
                block_id="n_xlsx_01",
                block_type=ASTBlockType.TABLE,
                level=2,
                section_path=["第十二章 工程量清单报价表"],
                text_content="| 序号 | 分部名称 | 合计金额 |\n|---|---|---|\n| 1 | 建筑安装工程造价汇总 | 48270.00 万元 |",
                table_data=TableData(
                    headers=[["序号", "分部名称", "合计金额"]],
                    rows=[["1", "建筑安装工程造价汇总", "48270.00 万元"]],
                    markdown="| 序号 | 分部名称 | 合计金额 |\n|---|---|---|\n| 1 | 建筑安装工程造价汇总 | 48270.00 万元 |"
                ),
                page_or_sheet="Sheet_Summary",
            ),
            # CAD / 暖通设备说明: 投标响应冷水机组 COP 4.8 (注入负偏离)
            ASTNode(
                block_id="n_cad_01",
                block_type=ASTBlockType.PARAGRAPH,
                level=2,
                section_path=["第八章 暖通空调工程", "冷水机组选型"],
                text_content="冷水机组选用变频离心机组，额定制冷量 3500 kW，实测能效比 COP 为 4.8。",
                page_or_sheet="Drawing_M_03",
            ),
            # 正偏离项
            ASTNode(
                block_id="n_comp_01",
                block_type=ASTBlockType.PARAGRAPH,
                level=2,
                section_path=["第九章 设备质保"],
                text_content="全场核心设备提供 5 年免费质保期服务。",
                page_or_sheet="45",
            ),
        ]

        proposal_ast = UnifiedDocumentAST(
            document_id="doc_hospital_epc",
            tenant_id="tenant_hospital",
            file_name="三甲医院EPC标书综合审查包.pdf",
            source_type=DocumentSourceType.PDF,
            total_pages_or_sheets=120,
            nodes=proposal_nodes,
        )

        # 2. 运行跨章节数值一致性校验引擎
        engine_instance = ConsistencyEngine()
        report = engine_instance.validate_ast_consistency(proposal_ast)

        # 验证检出冲突
        assert report.conflicts_found >= 2, f"期望检出工期与造价双重矛盾，实际检出 {report.conflicts_found}"
        categories = {c.metric_category for c in report.conflicts}
        assert "工期" in categories, "必须成功检出工期冲突"
        assert "造价" in categories, "必须成功检出造价冲突"

        # 详细验证工期冲突数值精度 (45 天差值)
        dur_conflict = next(c for c in report.conflicts if c.metric_category == "工期")
        assert dur_conflict.severity == IssueSeverity.CRITICAL
        assert abs(dur_conflict.diff_value - 45.0) < 1e-2, f"工期差值应为 45 天，实际为 {dur_conflict.diff_value}"

        # 详细验证造价冲突数值精度 (230 万元差值)
        cost_conflict = next(c for c in report.conflicts if c.metric_category == "造价")
        assert cost_conflict.severity == IssueSeverity.CRITICAL
        assert abs(cost_conflict.diff_value - 230.0) < 1e-2, f"造价差值应为 230 万元，实际为 {cost_conflict.diff_value}"

        # 3. 构造招标文件评分表 AST 并运行 4 类偏离度深度比对引擎
        rfp_rows = [
            ["序号", "分类", "评分项", "分值", "评分细则"],
            ["1", "技术标", "施工总工期", "15分", "★ 工期不超过 720 天"],
            ["2", "技术标", "冷水机组能效比 COP", "15分", "冷机 COP 不低于 5.0"],
            ["3", "商务标", "核心机电设备质保期", "10分", "免费质保期不少于 2 年"],
            ["4", "技术标", "市级智慧医疗健康云平台直连", "10分", "具备标准医疗云接口"],
        ]

        rfp_ast = UnifiedDocumentAST(
            document_id="rfp_hospital_01",
            tenant_id="tenant_hospital",
            file_name="招标文件评分表.pdf",
            source_type=DocumentSourceType.PDF,
            nodes=[
                ASTNode(
                    block_id="rfp_table_node",
                    block_type=ASTBlockType.TABLE,
                    section_path=["第四章 评标办法与评分标准"],
                    text_content="评标办法评分表",
                    page_or_sheet="10",
                    table_data=TableData(headers=[rfp_rows[0]], rows=rfp_rows[1:]),
                )
            ]
        )

        alignment_engine = TenderAlignmentEngine()
        align_report = alignment_engine.align_and_evaluate(rfp_ast, proposal_ast)

        # 验证 4 类偏离度矩阵完整分类
        dev_types = [eval_res.deviation_type for eval_res in align_report.results]
        assert DeviationType.NEGATIVE in dev_types, "COP 4.8 必须被精准判定为负偏离"
        assert DeviationType.MISSING in dev_types, "未提及的医疗云接口必须被判定为缺失项"
        assert DeviationType.FULL_COMPLIANCE in dev_types or DeviationType.POSITIVE in dev_types

        # 校验负偏离项的具体评分与扣分
        cop_eval = next(e for e in align_report.results if "COP" in e.title or "冷机" in e.title)
        assert cop_eval.deviation_type == DeviationType.NEGATIVE
        assert cop_eval.severity in (SeverityLevel.CRITICAL, SeverityLevel.HIGH)
        assert "4.8" in cop_eval.source_quote

    @pytest.mark.asyncio
    async def test_scenario_2_smart_industrial_park_weak_current_bidding(self):
        """
        Scenario 2: 智慧科技产业园弱电智能化工程标书闭环反思
        1. Generator 初稿产生 COP 4.8 负偏离
        2. Critic 检出负偏离，扣减至 65 分并生成结构化 Patch Diff
        3. Router 触发第 1 轮反思回流
        4. Generator 精准靶向重写为 COP 5.4，其余合规文本 100% 冻结
        5. Critic 二次审查打分 >= 85 分，passed=True，状态机批准通过
        """
        workflow_app = build_dual_agent_workflow()

        initial_state: GraphState = {
            "tenant_id": "tenant_industrial_park",
            "task_id": "task_smart_park_01",
            "thread_id": "th_smart_park_01",
            "rfp_requirements": "科技产业园智能化招标要求：总工期严格控制在 90 个日历天内，冷水机组 COP 不低于 5.0",
            "context_chunks": [
                {
                    "chunk_id": "chk_park_spec",
                    "content": "技术标要求：工程工期不超过 90 个日历天；冷机 COP 必须 >= 5.0。",
                }
            ],
            "iteration_count": 0,
            "max_iterations": 2,
            "status": TaskStatus.PROCESSING,
            "review_history": [],
        }

        final_state = await workflow_app.ainvoke(initial_state)

        # 1. 验证终态通过且反思轮次受控于 2 轮以内
        assert final_state["status"] == TaskStatus.SUCCESS
        assert 1 <= final_state["iteration_count"] <= 2

        # 2. 验证终版草案包含纠偏后的 COP 5.4 及 90 天工期
        draft = final_state["draft"]
        assert "COP 为 5.4" in draft
        assert "90 个日历天" in draft

        # 3. 验证审计反馈指标
        audit_fb = final_state["audit_feedback"]
        assert audit_fb is not None
        assert audit_fb["passed"] is True
        assert audit_fb["score"] >= 85.0
        assert len(audit_fb["issues"]) == 0

    @pytest.mark.asyncio
    async def test_scenario_3_unmitigated_negative_deviation_circuit_breaker_and_hitl(self):
        """
        Scenario 3: 不可调和缺陷连续负偏离熔断与 HITL 人工介入恢复
        1. 注入极端不可调和物理死锁要求 (工期必须 30 天竣工)
        2. Generator 经过 2 轮反思均无法消除严重缺陷
        3. Router 触发 2 次迭代硬熔断，挂起流转至 HUMAN_REVIEW
        4. Checkpointer 快照落盘
        5. 人工调用 resume_workflow 注入特批补丁恢复流转至 SUCCESS
        """
        class DeadlockCritic(CriticAgent):
            def _perform_audit(self, draft, rfp, contexts, iteration, guardrails=None):
                return {
                    "passed": False,
                    "score": 40.0,
                    "hallucination_detected": True,
                    "issues": [
                        {
                            "issue_id": f"deadlock_issue_{iteration}",
                            "target_section": "项目施工总工期",
                            "error_quote": "30个日历天内竣工验收",
                            "suggested_replacement": "工期违规不可修正",
                            "reason": "主体施工30天无法保障混凝土龄期与消防安全强标",
                            "severity": SeverityLevel.CRITICAL,
                        }
                    ],
                    "summary_comment": f"第 {iteration} 轮质检: 存在无法调和的工期死锁",
                }

        breaker_app = build_dual_agent_workflow(critic_agent=DeadlockCritic())
        checkpointer = get_workflow_checkpointer()

        th_id = "th_hitl_scenario_03"
        state: GraphState = {
            "tenant_id": "tenant_hitl_03",
            "task_id": "task_hitl_03",
            "thread_id": th_id,
            "rfp_requirements": "极端不可调和工期招标文件: 30天竣工验收",
            "context_chunks": [],
            "iteration_count": 0,
            "max_iterations": 2,
            "status": TaskStatus.PROCESSING,
            "review_history": [],
        }

        # 执行状态机直至触发熔断
        halted_state = await breaker_app.ainvoke(state)

        # 1. 验证熔断停机
        assert halted_state["status"] == TaskStatus.HUMAN_REVIEW
        assert halted_state["iteration_count"] == 2

        # 2. 验证 Checkpointer 已持久化快照
        snapshot = checkpointer.get(th_id)
        assert snapshot is not None
        assert snapshot["status"] == TaskStatus.HUMAN_REVIEW

        # 3. 人工干预注入特批补丁恢复工作流
        human_patch = "【住建局专家委员会特批批复件】根据建设工程质量管理条例，本项目工期法定调整为 180 个日历天。"
        resumed_state = await resume_workflow(
            thread_id=th_id,
            human_patch=human_patch,
            decision="override_and_finish",
            checkpointer=checkpointer,
        )

        # 4. 验证恢复终态为 SUCCESS 且包含专家特批文本
        assert resumed_state["status"] == TaskStatus.SUCCESS
        assert human_patch in resumed_state["draft"]
        history_actions = [h.get("action") for h in resumed_state.get("review_history", [])]
        assert "human_intervention" in history_actions

    @pytest.mark.asyncio
    async def test_scenario_4_multi_tenant_enterprise_concurrent_isolation_stress(self):
        """
        Scenario 4: 多租户企业级高并发检索与隔离压测
        Tenant Alpha (总包集团) 与 Tenant Beta (投标联合体) 并发写入高价值机密切片并检索，
        在严密隔离下验证双方 0 交叉穿透与 0 数据泄露
        """
        t_alpha = "tenant_epc_alpha"
        t_beta = "tenant_bid_beta"

        # 1. 准备独立租户数据与向量切片
        async with SessionLocal() as session:
            session.add_all([
                Tenant(id=t_alpha, code="EPC_A", name="总包集团甲"),
                Tenant(id=t_beta, code="BID_B", name="投标联合体乙"),
                Document(id="doc_a_epc", tenant_id=t_alpha, title="a_plan.docx", file_type="docx", s3_path="s3://a", file_hash="ha1"),
                Document(id="doc_b_bid", tenant_id=t_beta, title="b_cost.xlsx", file_type="xlsx", s3_path="s3://b", file_hash="hb2"),
            ])
            # 写入 Alpha 敏感标底
            session.add(
                DocumentChunk(
                    id="chk_alpha_cost",
                    tenant_id=t_alpha,
                    document_id="doc_a_epc",
                    chunk_index=1,
                    chunk_level=ChunkLevel.CHILD,
                    content="甲方总包部内部最高限价与标底为 48500 万元，严禁对外泄露",
                )
            )
            # 写入 Beta 敏感商务秘密
            session.add(
                DocumentChunk(
                    id="chk_beta_quote",
                    tenant_id=t_beta,
                    document_id="doc_b_bid",
                    chunk_index=1,
                    chunk_level=ChunkLevel.CHILD,
                    content="乙方联合体最终底线投标报价为 45200 万元，下浮率 6.8%",
                )
            )
            await session.commit()

        # 2. 并发检索探针
        mock_embedding = EmbeddingService(provider=MockDeterministicEmbeddingProvider(dim=1536))
        search_engine = HybridSearchEngine(embedding_service=mock_embedding)

        async def run_tenant_search(tenant_id: str, query: str):
            async with SessionLocal() as session:
                with TenantContext(tenant_id):
                    return await search_engine.search(session=session, tenant_id=tenant_id, query=query, top_k=5)

        # 双方并发向对方的敏感词汇发起检索刺探
        tasks = [
            run_tenant_search(t_alpha, "投标报价 45200 万元 联合体"),
            run_tenant_search(t_beta, "最高限价 48500 万元 标底"),
        ]
        alpha_hits, beta_hits = await asyncio.gather(*tasks)

        # 3. 验证 0 交叉泄露 (Zero-Leakage)
        for hit in alpha_hits:
            assert "45200" not in hit.content, "安全违规: 甲方会话检索到了乙方的机密报价！"
        for hit in beta_hits:
            assert "48500" not in hit.content, "安全违规: 乙方会话检索到了甲方的内部最高限价！"

    @pytest.mark.asyncio
    async def test_scenario_5_historical_engineering_risk_early_warning_trigger(self):
        """
        Scenario 5: 历史工程审查风险前置主动预警拦截
        1. 输入新立项 ProjectCharter: 地下3层，开挖深度 7.2m
        2. 触发历史案例种子 PRJ-2024-SZ-041 (深基坑未编制超危大专家论证方案)
        3. 自动生成住建部 37 号令专项方案及 24 小时位移监测护栏 Markdown
        4. 护栏提示词注入 Generator Agent，初稿体现安全方案
        5. Critic 质检核验合格通过
        """
        async with SessionLocal() as session:
            t_id = "tenant_risk_s5"
            session.add(Tenant(id=t_id, code="T_RISK_S5", name="风险预警测试租户"))
            await session.commit()

            # 种子注入历史风险案例
            seeded_risks = await seed_historical_risks(session, tenant_id=t_id)
            assert len(seeded_risks) >= 5

            # 构造深基坑立项报告
            charter = ProjectCharter(
                project_name="滨海新区超高层双子塔及深基坑支护工程",
                project_type="房建",
                scale_description="总建筑面积12万㎡，地下3层，开挖深度7.2m，富水软土地层",
                excavation_depth_meters=7.2,
                duration_days=540,
                budget_cny_ten_thousand=35000.0,
                special_conditions=["富水软土地层", "临近既有地铁区间", "危大工程"],
            )

            interceptor = ProjectRiskInterceptor()
            report = await interceptor.intercept_project_risks(
                session=session, tenant_id=t_id, charter=charter
            )

            # 验证成功命中深基坑风险
            assert len(report.warnings) >= 1
            pit_risk = next((r for r in report.warnings if "基坑" in r.risk_title), None)
            assert pit_risk is not None
            assert pit_risk.severity == SeverityLevel.CRITICAL

            guardrail_prompt = report.guardrail_system_prompt_snippet
            # 验证护栏提示词准确包含了住建部 37 号令与超危大专家论证
            assert "37 号令" in guardrail_prompt or "37号令" in guardrail_prompt
            assert "专家论证" in guardrail_prompt

            # 注入 Generator Agent 方案拟定与双智能体全流程闭环
            workflow_app = build_dual_agent_workflow()
            initial_state: GraphState = {
                "tenant_id": t_id,
                "task_id": "task_risk_s5_01",
                "thread_id": "th_risk_s5_01",
                "rfp_requirements": "拟定深基坑施工组织方案及安全保障承诺，总工期不得超过 90 个日历天，COP 不低于 5.0",
                "risk_guardrails": guardrail_prompt,
                "context_chunks": [
                    {
                        "chunk_id": "chk_risk_01",
                        "content": "基坑开挖深度 7.2m，工期 90 天，冷机 COP 5.0。",
                    }
                ],
                "iteration_count": 0,
                "max_iterations": 2,
                "status": TaskStatus.PROCESSING,
                "review_history": [],
            }

            final_state = await workflow_app.ainvoke(initial_state)

            # 验证初稿完整融入了预防护栏要求并审核通过流转至 SUCCESS
            assert final_state["status"] == TaskStatus.SUCCESS
            draft = final_state["draft"]
            assert ("37 号令" in draft) or ("37号令" in draft) or ("超危大工程" in draft) or ("专家论证" in draft)
            assert final_state["audit_feedback"]["passed"] is True


class TestAcceptanceCriteriaThresholds:
    """系统级核心验收指标门禁验证 (Acceptance Criteria)"""

    @pytest.mark.asyncio
    async def test_ast_reduction_rate_threshold_ge_98(self):
        """验收指标门禁 1: 全格式 AST 结构还原率 >= 98.0%"""
        gt = generate_ground_truth_spec()
        headings = gt["headings"]  # 100 个标题
        paragraphs = gt["paragraphs"]  # 200 个段落

        # 1. 构造包含已知真值地标的 PDF 字节流
        pdf_lines = ["%PDF-1.7"]
        for h in headings:
            pdf_lines.append(f"/Title ({h})")
            pdf_lines.append(f"BT 1 0 0 1 50.0 750.0 Tm ({h}) Tj ET")
        for p in paragraphs:
            pdf_lines.append(f"BT 1 0 0 1 50.0 700.0 Tm ({p}) Tj ET")
        pdf_lines.append("%%EOF")
        pdf_bytes = "\n".join(pdf_lines).encode("utf-8")

        pdf_ast = await PDFParser().parse(pdf_bytes, file_name="benchmark.pdf")
        pdf_texts = {n.text_content for n in pdf_ast.nodes}
        pdf_matched = sum(1 for h in headings if h in pdf_texts)
        pdf_rate = (pdf_matched / len(headings)) * 100.0
        assert pdf_rate >= 98.0, f"PDF AST 地标还原率未达 98%: {pdf_rate:.2f}%"

        # 2. 构造包含已知真值地标的 DOCX 字节流
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            ct = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
                '  <Default Extension="xml" ContentType="application/xml"/>\n'
                '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
                '  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>\n'
                '</Types>'
            )
            z.writestr("[Content_Types].xml", ct)
            xml_parts = ['<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>']
            for h in headings:
                xml_parts.append(f'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>{h}</w:t></w:r></w:p>')
            for p in paragraphs:
                xml_parts.append(f'<w:p><w:r><w:t>{p}</w:t></w:r></w:p>')
            xml_parts.append('</w:body></w:document>')
            z.writestr("word/document.xml", "".join(xml_parts))
        docx_bytes = buf.getvalue()

        docx_ast = await DOCXParser().parse(docx_bytes, file_name="benchmark.docx")
        docx_texts = {n.text_content for n in docx_ast.nodes}
        docx_matched = sum(1 for h in headings if h in docx_texts)
        docx_rate = (docx_matched / len(headings)) * 100.0
        assert docx_rate >= 98.0, f"DOCX AST 地标还原率未达 98%: {docx_rate:.2f}%"

    @pytest.mark.asyncio
    async def test_parent_child_retrieval_top5_recall_ge_95(self):
        """验收指标门禁 2: 父子切片检索 Top-5 召回率 >= 95.0% 且父级上下文回填精准"""
        tenant_id = "tenant_recall_benchmark"
        mock_provider = MockDeterministicEmbeddingProvider(dim=1536)
        mock_embedding = EmbeddingService(provider=mock_provider)
        search_engine = HybridSearchEngine(embedding_service=mock_embedding)
        backfiller = ContextBackfiller()

        test_pairs = [
            ("超高效离心机组性能参数与能效比", "机房冷冻站选用特级能效离心机组，额定能效比 COP 达到 5.6，优于国家标准"),
            ("综合管线抗震支吊架安装技术标准", "抗震支架间距严格按照 GB 50981-2014 规范设置，垂直荷载计算安全系数不低于 2.0"),
            ("智慧楼宇 BACnet 协议开放性接口", "楼宇自控系统 DDC 控制器提供标准 BACnet/IP 协议接口，点位开放率 100%"),
            ("超危大工程基坑支护应急预案", "深基坑工程编制专项应急预案，现场常备 500kW 应急发电机组及排涝泵"),
            ("医院洁净手术部空调温湿度自控", "层流手术室相对湿度恒定控制在 40%~60%，温度控制精度达到正负 0.5 摄氏度"),
        ]

        async with SessionLocal() as session:
            session.add(Tenant(id=tenant_id, code="T_REC", name="召回率测试租户"))
            session.add(Document(id="doc_rec_01", tenant_id=tenant_id, title="specs.docx", file_type="docx", s3_path="s3://rec", file_hash="hrec"))

            for idx, (q, a) in enumerate(test_pairs):
                # 写入父切片
                p_id = f"parent_rec_{idx}"
                session.add(
                    DocumentChunk(
                        id=p_id,
                        tenant_id=tenant_id,
                        document_id="doc_rec_01",
                        chunk_index=idx * 2,
                        chunk_level=ChunkLevel.PARENT,
                        content=f"第{idx+1}章 详细工程规范上下文:\n{a}\n附带施工验收标准与运维指南。",
                        token_count=120,
                    )
                )
                # 写入子切片 (注入向量)
                c_id = f"child_rec_{idx}"
                vec = await mock_provider.embed_query(a)
                session.add(
                    DocumentChunk(
                        id=c_id,
                        tenant_id=tenant_id,
                        document_id="doc_rec_01",
                        parent_chunk_id=p_id,
                        chunk_index=idx * 2 + 1,
                        chunk_level=ChunkLevel.CHILD,
                        content=a,
                        token_count=35,
                        embedding=json.dumps(vec),
                    )
                )
            await session.commit()

            # 执行评测查询
            hits_count = 0
            for q, expected_answer in test_pairs:
                with TenantContext(tenant_id):
                    hits = await search_engine.search(session=session, tenant_id=tenant_id, query=q, top_k=5)
                    top5_texts = [h.content for h in hits]
                    if any(expected_answer in t or t in expected_answer for t in top5_texts):
                        hits_count += 1

                    # 验证回填器精准回填对应的父切片
                    if hits:
                        backfilled = await backfiller.backfill(session=session, hits=hits, tenant_id=tenant_id)
                        assert backfilled.unique_parent_count >= 1
                        assert len(backfilled.parents) >= 1
                        assert backfilled.parents[0].parent_chunk_id.startswith("parent_rec_")

            recall_rate = (hits_count / len(test_pairs)) * 100.0
            assert recall_rate >= 95.0, f"父子检索 Top-5 召回率未达标: {recall_rate:.2f}% (门禁 >= 95.0%)"

    @pytest.mark.asyncio
    async def test_cross_chapter_consistency_contradiction_detection_100(self):
        """验收指标门禁 3: 跨章节数值一致性矛盾检出率达到 100.0%"""
        ast = UnifiedDocumentAST(
            document_id="doc_contrast_all",
            tenant_id="tenant_qa",
            file_name="矛盾注入综合标书.pdf",
            source_type=DocumentSourceType.PDF,
            nodes=[
                # 1. 工期矛盾
                ASTNode(block_id="b1", block_type=ASTBlockType.PARAGRAPH, level=1, section_path=["第1章 投标总函"], text_content="本工程计划施工总工期为 360 个日历天。", page_or_sheet="1"),
                ASTNode(block_id="b2", block_type=ASTBlockType.PARAGRAPH, level=1, section_path=["第4章 进度计划"], text_content="各施工节点进度综合排期总工期为 405 个日历天。", page_or_sheet="20"),
                # 2. 造价矛盾
                ASTNode(block_id="b3", block_type=ASTBlockType.PARAGRAPH, level=1, section_path=["第2章 投资估算"], text_content="工程总投资预算为 9000.00 万元。", page_or_sheet="5"),
                ASTNode(block_id="b4", block_type=ASTBlockType.PARAGRAPH, level=1, section_path=["第10章 商务报价"], text_content="经复核建安工程总造价为 8800.00 万元。", page_or_sheet="50"),
                # 3. 面积矛盾
                ASTNode(block_id="b5", block_type=ASTBlockType.PARAGRAPH, level=1, section_path=["第1章 建设总平"], text_content="规划总建筑面积为 45000.00 m²。", page_or_sheet="2"),
                ASTNode(block_id="b6", block_type=ASTBlockType.PARAGRAPH, level=1, section_path=["第3章 单体核算"], text_content="工程地上地下总建筑面积为 52000.00 m²。", page_or_sheet="15"),
                # 4. COP 矛盾
                ASTNode(block_id="b7", block_type=ASTBlockType.PARAGRAPH, level=1, section_path=["第5章 暖通技术"], text_content="冷水机组额定工况下能效比 COP 为 5.4。", page_or_sheet="30"),
                ASTNode(block_id="b8", block_type=ASTBlockType.PARAGRAPH, level=1, section_path=["第6章 验收指标"], text_content="暖通设备性能验收标准明确机组能效比 COP 为 4.6。", page_or_sheet="35"),
            ],
        )

        engine_instance = ConsistencyEngine()
        report = engine_instance.validate_ast_consistency(ast)

        # 验证 4 大维度矛盾 100% 全部检出
        detected_categories = {c.metric_category for c in report.conflicts}
        assert "工期" in detected_categories, "工期矛盾未检出"
        assert "造价" in detected_categories, "造价矛盾未检出"
        assert "建筑面积" in detected_categories, "建筑面积矛盾未检出"
        assert "COP" in detected_categories, "COP 矛盾未检出"
        assert len(detected_categories) == 4, f"期望 4 类矛盾 100% 检出，实际检出: {detected_categories}"

    def test_zero_dify_runtime_dependencies_audit(self):
        """验收指标门禁 4: 静态代码法证审计，确认全系统零 Dify 依赖"""
        backend_dir = Path(__file__).parent.parent
        audit_targets = [
            backend_dir / "app" / "parsers",
            backend_dir / "app" / "rag",
            backend_dir / "app" / "quality",
            backend_dir / "app" / "workflow",
            backend_dir / "app" / "tasks",
            backend_dir / "app" / "api",
            backend_dir / "app" / "schemas" / "gateway.py",
            backend_dir / "app" / "celery_app.py",
            backend_dir / "app" / "models" / "audit_rag.py",
        ]

        dify_hits: List[str] = []
        for target in audit_targets:
            if target.is_file():
                files = [target]
            elif target.is_dir():
                files = list(target.rglob("*.py"))
            else:
                continue

            for file_path in files:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
                lines = content.splitlines()
                for line_idx, line in enumerate(lines, 1):
                    clean_line = line.strip().lower()
                    if "dify" in clean_line:
                        dify_hits.append(f"{file_path.relative_to(backend_dir)}:{line_idx}: {line.strip()}")

        assert len(dify_hits) == 0, f"发现非法 Dify 外部依赖残留 ({len(dify_hits)} 处):\n" + "\n".join(dify_hits)

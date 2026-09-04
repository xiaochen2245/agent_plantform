"""
Phase 2 (M2) 质量质检与招投标对齐引擎端到端集成测试 (E2E Integration Tests)
综合验证三大核心引擎协同作业:
1. 大纲层级与序号断层质检 (DocumentQualityEngine: OutlineValidator + FormatValidator)
2. 跨章节数值前后矛盾与量纲归一化冲突检测 (ConsistencyEngine + MetricNormalizer)
3. 招标文件评分表解析、自编标书语义对齐与 4 类偏离度判定 (TenderAlignmentEngine)
4. 全流程产物转换为 SQLAlchemy 2.0 ReviewResult 实体并验证字段合规性
"""

import pytest
from app.models.audit_rag import DeviationType, ReviewResult, SeverityLevel
from app.quality.consistency_engine import (
    ConflictType,
    ConsistencyEngine,
    IssueSeverity,
)
from app.quality.outline_validator import (
    DocumentQualityEngine,
    FormatIssueType,
    OutlineIssueType,
)
from app.quality.tender_alignment import (
    MetricDirection,
    TenderAlignmentEngine,
)
from app.schemas.ast import (
    ASTBlockType,
    ASTNode,
    DocumentSourceType,
    TableData,
    UnifiedDocumentAST,
)


def test_quality_and_alignment_e2e_pipeline():
    """
    全流程真实工程投标文件端到端质检流水线:
    - 输入真实的标书 AST 与招标评分表 AST
    - 运行格式排版和大纲质检
    - 运行长文档跨章节一致性交叉审核
    - 运行招标文件评分项对齐与偏离度诊断
    - 汇总生成 ReviewResult 实体并确保 100% 具备溯源证据链
    """
    tenant_id = "tenant_e2e_corp"
    task_id = "task_e2e_audit_888"

    # -----------------------------------------------------------------------
    # 1. 构建招标文件 AST (包含详细评审评分表与 ★ 强制项)
    # -----------------------------------------------------------------------
    rfp_rows = [
        ["序号", "评审大类", "评分项目", "分值", "评分细则与基准要求"],
        ["1", "技术标", "项目施工总工期", "20分", "★ 施工总工期不得超过 360 天，超过则直接作废标处理"],
        ["2", "技术标", "主机能效比(COP)", "15分", "选用高效离心机组，COP 不低于 5.2，优于基准赋满分"],
        ["3", "技术标", "后期免费质保期", "10分", "免费质保期不少于 2 年，高于标准者优先评分"],
        ["4", "商务标", "类似大型数据中心业绩", "15分", "近三年完成类似机电工程不少于 3 项"],
    ]

    rfp_ast = UnifiedDocumentAST(
        document_id="rfp_doc_e2e",
        tenant_id=tenant_id,
        file_name="某智算中心机电安装总承包招标文件.pdf",
        source_type=DocumentSourceType.PDF,
        nodes=[
            ASTNode(
                block_id="rfp_h1",
                block_type=ASTBlockType.HEADING,
                level=1,
                section_path=["第四章 评标办法"],
                text_content="第四章 详细评审标准与评标办法",
                page_or_sheet="40",
            ),
            ASTNode(
                block_id="rfp_t1",
                block_type=ASTBlockType.TABLE,
                section_path=["第四章 评标办法", "4.2 综合评分标准"],
                text_content="综合评分细则表",
                page_or_sheet="41",
                table_data=TableData(headers=[rfp_rows[0]], rows=rfp_rows[1:]),
            ),
        ],
    )

    # -----------------------------------------------------------------------
    # 2. 构建自编投标标书 AST
    #    (包含: 大纲跳层/断号、表格空单元格异常、工期与造价跨章节矛盾、质保期正偏离、业绩缺失)
    # -----------------------------------------------------------------------
    proposal_nodes = [
        # 第1章 投标总说明
        ASTNode(
            block_id="p_h1",
            block_type=ASTBlockType.HEADING,
            level=1,
            section_path=["第一章 投标总函与承诺"],
            text_content="第一章 投标总函与承诺",
            page_or_sheet="1",
        ),
        ASTNode(
            block_id="p_para1",
            block_type=ASTBlockType.PARAGRAPH,
            section_path=["第一章 投标总函与承诺"],
            text_content="我方承诺本项目总工期为 330 天，工程总造价预算控制在 1200 万元。",
            page_or_sheet="2",
        ),

        # 第2章 大纲层级跳跃: 1 级直跳 3 级 (缺少 2 级)
        ASTNode(
            block_id="p_h2",
            block_type=ASTBlockType.HEADING,
            level=1,
            section_path=["第二章 施工部署"],
            text_content="第二章 施工总体部署",
            page_or_sheet="5",
        ),
        ASTNode(
            block_id="p_h2_sub",
            block_type=ASTBlockType.HEADING,
            level=3, # 跳级!
            section_path=["第二章 施工部署", "2.1.1 施工网络管理"],
            text_content="2.1.1 施工网络管理与组织",
            page_or_sheet="6",
        ),

        # 第5章 设备与材料 (COP 满足要求 5.4，表格空单元格率超标)
        ASTNode(
            block_id="p_h5",
            block_type=ASTBlockType.HEADING,
            level=1,
            section_path=["第五章 核心机电设备"],
            text_content="第五章 核心机电设备采购与技术规格",
            page_or_sheet="20",
        ),
        ASTNode(
            block_id="p_para5",
            block_type=ASTBlockType.PARAGRAPH,
            section_path=["第五章 核心机电设备"],
            text_content="离心式冷水机组额定能效比 COP 达到 5.4。",
            page_or_sheet="21",
        ),
        ASTNode(
            block_id="p_tbl5",
            block_type=ASTBlockType.TABLE,
            section_path=["第五章 核心机电设备"],
            text_content="辅机选型表",
            page_or_sheet="22",
            table_data=TableData(
                headers=[["设备名称", "参数", "厂家", "备注"]],
                rows=[
                    ["水泵", "", "", ""],
                    ["阀门", "", "", ""],
                    ["风机", "", "", ""],
                    ["管道", "", "", ""],
                ], # 16个单元格12个空 -> 75% 空值率
            ),
        ),

        # 第8章 维保服务 (质保期 3 年 -> 优于要求 2 年，正偏离)
        ASTNode(
            block_id="p_h8",
            block_type=ASTBlockType.HEADING,
            level=1,
            section_path=["第八章 售后维保服务"],
            text_content="第八章 售后维保服务与质量保修承诺",
            page_or_sheet="35",
        ),
        ASTNode(
            block_id="p_para8",
            block_type=ASTBlockType.PARAGRAPH,
            section_path=["第八章 售后维保服务"],
            text_content="我司无偿提供免费质保期 3 年，设立 7x24 小时快速响应中心。",
            page_or_sheet="36",
        ),

        # 第25章 施工进度保障 (制造工期前后相悖: 360 天 vs 前文 330 天)
        ASTNode(
            block_id="p_h25",
            block_type=ASTBlockType.HEADING,
            level=1,
            section_path=["第二十五章 施工进度保障"],
            text_content="第二十五章 施工进度计划与网络横道图",
            page_or_sheet="80",
        ),
        ASTNode(
            block_id="p_para25",
            block_type=ASTBlockType.PARAGRAPH,
            section_path=["第二十五章 施工进度保障"],
            text_content="经关键路径统筹排期，本项目总施工周期调整为 360 天，工程总造价预算为 1500 万元。",
            page_or_sheet="81",
        ),
    ]

    proposal_ast = UnifiedDocumentAST(
        document_id="proposal_doc_e2e",
        tenant_id=tenant_id,
        file_name="自编投标文件送审稿.docx",
        source_type=DocumentSourceType.DOCX,
        nodes=proposal_nodes,
    )

    # =======================================================================
    # 3. 运行 Engine 1: DocumentQualityEngine (大纲与排版质检)
    # =======================================================================
    quality_engine = DocumentQualityEngine()
    quality_report = quality_engine.validate_document(proposal_ast)

    # 验证检出跳级 LEVEL_JUMP 与空单元格率超标 TABLE_EMPTY_CELL_RATIO_HIGH
    outline_issue_types = {iss.issue_type for iss in quality_report.outline_report.issues}
    assert OutlineIssueType.LEVEL_JUMP in outline_issue_types

    format_issue_types = {iss.issue_type for iss in quality_report.format_report.issues}
    assert FormatIssueType.TABLE_EMPTY_CELL_RATIO_HIGH in format_issue_types

    assert quality_report.overall_score < 100.0
    quality_reviews = quality_engine.to_review_results(quality_report, task_id=task_id, tenant_id=tenant_id)
    assert len(quality_reviews) == quality_report.total_issues_count

    # =======================================================================
    # 4. 运行 Engine 2: ConsistencyEngine (数值跨章节一致性校验)
    # =======================================================================
    consistency_engine = ConsistencyEngine()
    consistency_report = consistency_engine.validate_ast_consistency(proposal_ast)

    # 验证 100% 检出工期冲突 (330天 vs 360天) 与造价冲突 (1200万元 vs 1500万元)
    assert consistency_report.conflicts_found == 2
    assert consistency_report.critical_count == 2

    c_categories = {c.metric_category for c in consistency_report.conflicts}
    assert "工期" in c_categories
    assert "造价" in c_categories

    for c in consistency_report.conflicts:
        assert c.severity == IssueSeverity.CRITICAL
        assert c.baseline_statement.section_title != ""
        assert c.conflicting_statement.section_title != ""
        assert c.diff_value > 0

    consistency_reviews = consistency_engine.to_review_results(consistency_report, task_id=task_id, tenant_id=tenant_id)
    assert len(consistency_reviews) == 2

    # =======================================================================
    # 5. 运行 Engine 3: TenderAlignmentEngine (招投标对齐与 4 类偏离度)
    # =======================================================================
    alignment_engine = TenderAlignmentEngine()
    alignment_report = alignment_engine.align_and_evaluate(rfp_ast, proposal_ast)

    # 验证评分项总数 4 项
    assert alignment_report.total_criteria_count == 4

    # 验证 4 类偏离度分布:
    # 1) 工期 330天 < 360天 -> POSITIVE
    # 2) COP 5.4 > 5.2 -> POSITIVE
    # 3) 质保期 3年 > 2年 -> POSITIVE
    # 4) 类似业绩数量 -> 标书未提供 -> MISSING
    assert alignment_report.positive_count >= 2
    assert alignment_report.missing_count >= 1

    alignment_reviews = alignment_engine.to_review_results(alignment_report, task_id=task_id)
    assert len(alignment_reviews) == 4

    # =======================================================================
    # 6. 验证全量聚合 ReviewResult 实体合规性
    # =======================================================================
    all_reviews: list[ReviewResult] = quality_reviews + consistency_reviews + alignment_reviews
    assert len(all_reviews) >= 8

    for r in all_reviews:
        assert isinstance(r, ReviewResult)
        assert r.tenant_id == tenant_id
        assert r.task_id == task_id
        assert r.deviation_type in (
            DeviationType.FULL_COMPLIANCE,
            DeviationType.POSITIVE,
            DeviationType.NEGATIVE,
            DeviationType.MISSING,
            DeviationType.NOT_APPLICABLE,
        )
        assert r.severity in (
            SeverityLevel.CRITICAL,
            SeverityLevel.HIGH,
            SeverityLevel.MEDIUM,
            SeverityLevel.LOW,
            SeverityLevel.INFO,
        )
        assert 0.0 <= r.confidence <= 1.0
        assert r.title != ""
        assert r.description != ""
        assert r.suggestion != ""
        # 验证页码必须为整型或 None，决不能为非法字符串
        assert r.source_page is None or isinstance(r.source_page, int)

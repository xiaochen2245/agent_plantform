"""
招投标评分表深度比对与 4 类偏离度判定引擎单元测试
覆盖 Features 20, 21, 22:
1. 招标文件评分表结构化拆解 (RFPScoringTableParser)
2. 自编标书语义对齐与原文原句抽取 (BidSemanticAlignmentEngine)
3. 4 类偏离度判定与置信度打分 (FourCategoryDeviationClassifier: FULL_COMPLIANCE, MISSING, POSITIVE, NEGATIVE)
4. 综合对齐引擎与 SQLAlchemy ReviewResult 映射 (TenderAlignmentEngine)
"""

import pytest
from app.models.audit_rag import DeviationType, ReviewResult, SeverityLevel
from app.quality.tender_alignment import (
    AlignmentCandidate,
    BidSemanticAlignmentEngine,
    CriteriaConstraint,
    DeviationEvaluationResult,
    FourCategoryDeviationClassifier,
    MetricDirection,
    RFPScoringTableParser,
    ScoringCategory,
    TenderAlignmentEngine,
    TenderAlignmentReport,
    TenderScoringItem,
    TenderScoringTable,
)
from app.schemas.ast import (
    ASTBlockType,
    ASTNode,
    DocumentSourceType,
    TableData,
    UnifiedDocumentAST,
)


# ===========================================================================
# 1. RFP 评分表结构化拆解测试 (Feature 20)
# ===========================================================================

def test_rfp_scoring_table_parser_table_spans():
    """验证包含跨行合并大项、分值及评分细则的标准评分表解析"""
    table_rows = [
        ["序号", "评审类别", "评审项目", "满分", "评分标准与细则"],
        ["1", "商务标", "企业资质与信用", "10分", "具备电力工程施工总承包特级资质得10分，一级得6分"],
        ["2", "商务标", "类似工程业绩", "10分", "近3年内具备3项及以上类似数据中心机电工程业绩得10分，少于3项每少1项扣3分"],
        ["3", "技术标", "施工总工期与节点进度", "15分", "★ 施工总工期不得超过 360 天，满足要求得15分，超过则作废标处理"],
        ["4", "技术标", "核心机电设备能效方案", "15分", "冷水机组额定能效比 COP 不低于 5.0，优于要求每提高0.1加1分"],
        ["5", "技术标", "项目后期质量保修承诺", "10分", "免费质保期不少于 2 年，高于标准者优先赋满分"],
    ]

    ast = UnifiedDocumentAST(
        document_id="rfp_doc_001",
        tenant_id="tenant_gov_01",
        file_name="某大数据中心机电工程招标文件.pdf",
        source_type=DocumentSourceType.PDF,
        nodes=[
            ASTNode(
                block_id="node_rfp_title",
                block_type=ASTBlockType.HEADING,
                level=1,
                section_path=["第四章 评标办法"],
                text_content="第四章 详细评审办法与综合评分标准",
                page_or_sheet="32",
            ),
            ASTNode(
                block_id="node_rfp_tbl",
                block_type=ASTBlockType.TABLE,
                section_path=["第四章 评标办法", "4.2 详细评审"],
                text_content="综合评分标准表",
                page_or_sheet="33",
                table_data=TableData(headers=[table_rows[0]], rows=table_rows[1:]),
            ),
        ],
    )

    parser = RFPScoringTableParser()
    table = parser.parse_rfp_scoring_table(ast)

    assert len(table.items) == 5
    assert table.total_max_score == 60.0

    # 验证强制性废标条款识别
    dur_item = next(it for it in table.items if "工期" in it.name)
    assert dur_item.is_mandatory is True
    assert dur_item.max_score == 15.0
    assert dur_item.constraint is not None
    assert dur_item.constraint.metric_name == "工期"
    assert dur_item.constraint.target_value == 360.0
    assert dur_item.constraint.direction == MetricDirection.LOWER_BETTER

    # 验证 COP 指标识别
    cop_item = next(it for it in table.items if "COP" in it.name or "能效" in it.name)
    assert cop_item.is_mandatory is False
    assert cop_item.constraint is not None
    assert cop_item.constraint.metric_name == "COP"
    assert cop_item.constraint.target_value == 5.0
    assert cop_item.constraint.direction == MetricDirection.HIGHER_BETTER

    # 验证质保期指标识别
    war_item = next(it for it in table.items if "保修" in it.name or "质保" in it.name)
    assert war_item.constraint is not None
    assert war_item.constraint.metric_name == "质保期"
    assert war_item.constraint.target_value == 2.0


def test_rfp_scoring_table_parser_text_fallback():
    """验证非表格大纲文本格式评分标准回退解析"""
    ast = UnifiedDocumentAST(
        document_id="rfp_txt_doc",
        tenant_id="tenant_gov_01",
        file_name="非表格招标文件.docx",
        source_type=DocumentSourceType.DOCX,
        nodes=[
            ASTNode(
                block_id="n1",
                block_type=ASTBlockType.HEADING,
                level=2,
                section_path=["评标细则"],
                text_content="1. 施工总进度计划与工期保障措施（10分） 施工工期不超过 300 日历天，延误一日扣1分",
                page_or_sheet="12",
            ),
            ASTNode(
                block_id="n2",
                block_type=ASTBlockType.HEADING,
                level=2,
                section_path=["评标细则"],
                text_content="2. 类似工程业绩与信誉（10分） ★ 近三年同类业绩不少于 2 项，少于2项直接作废标处理",
                page_or_sheet="13",
            ),
        ],
    )
    parser = RFPScoringTableParser()
    table = parser.parse_rfp_scoring_table(ast)

    assert len(table.items) == 2
    item_proj = next(it for it in table.items if "业绩" in it.name)
    assert item_proj.is_mandatory is True
    assert item_proj.max_score == 10.0


# ===========================================================================
# 2. 自编标书语义对齐引擎测试 (Feature 21)
# ===========================================================================

def test_bid_semantic_alignment_engine():
    """验证标书章节与评分项跨文档拓扑对齐及证据原句定位"""
    proposal_ast = UnifiedDocumentAST(
        document_id="bid_doc_001",
        tenant_id="tenant_bidder_01",
        file_name="自编投标文件技术标.docx",
        source_type=DocumentSourceType.DOCX,
        nodes=[
            ASTNode(
                block_id="b_sec1",
                block_type=ASTBlockType.HEADING,
                level=1,
                section_path=["第3章 施工总进度计划"],
                text_content="第3章 施工总进度计划与工期保障措施",
                page_or_sheet="18",
            ),
            ASTNode(
                block_id="b_p1",
                block_type=ASTBlockType.PARAGRAPH,
                section_path=["第3章 施工总进度计划", "3.1 工期承诺"],
                text_content="我公司充分组织优势资源，郑重承诺施工总工期为 330 日历天，提前交付。",
                page_or_sheet="19",
            ),
            ASTNode(
                block_id="b_p2",
                block_type=ASTBlockType.PARAGRAPH,
                section_path=["第5章 暖通节能方案", "5.2 机组能效"],
                text_content="本工程选用高效永磁变频直驱离心式冷水机组，经国家质检中心检测额定 COP 达到 5.6。",
                page_or_sheet="45",
            ),
        ],
    )

    alignment_engine = BidSemanticAlignmentEngine(proposal_ast)

    # 1. 匹配工期评分项
    dur_item = TenderScoringItem(
        criteria_id="CRIT_01",
        category=ScoringCategory.TECHNICAL,
        name="施工总工期与节点进度",
        max_score=15.0,
        scoring_guide="工期不超过 360 天",
        constraint=CriteriaConstraint(
            metric_name="工期",
            target_value=360.0,
            target_unit="天",
            direction=MetricDirection.LOWER_BETTER,
        ),
        keywords=["工期", "进度", "计划"],
    )
    cand_dur = alignment_engine.align_item(dur_item)
    assert cand_dur.is_matched is True
    assert cand_dur.alignment_score >= 0.25
    assert cand_dur.node_id == "b_p1"
    assert "330" in cand_dur.matched_quote
    assert cand_dur.page_number == 19

    # 2. 匹配标书中未包含的评分项 (如 绿色文明施工与防尘降噪)
    green_item = TenderScoringItem(
        criteria_id="CRIT_MISS",
        category=ScoringCategory.TECHNICAL,
        name="绿色文明施工与扬尘水土噪音防控措施",
        max_score=10.0,
        scoring_guide="提供完善的三废治理与绿色建筑三星级施工保障方案",
        keywords=["扬尘", "降噪", "水土保持", "三废"],
    )
    cand_miss = alignment_engine.align_item(green_item)
    assert cand_miss.is_matched is False


# ===========================================================================
# 3. 4 类偏离度分类器综合测试 (Feature 22)
# ===========================================================================

def test_4_category_deviation_full_compliance():
    """验证标书承诺完全满足招标文件基准要求 (FULL_COMPLIANCE)"""
    classifier = FourCategoryDeviationClassifier()

    item = TenderScoringItem(
        criteria_id="CRIT_WAR",
        category=ScoringCategory.TECHNICAL,
        name="工程后期质量保修方案",
        max_score=10.0,
        scoring_guide="免费质保期不少于 2 年",
        constraint=CriteriaConstraint(
            metric_name="质保期",
            target_value=2.0,
            target_unit="年",
            direction=MetricDirection.HIGHER_BETTER,
        ),
    )
    candidate = AlignmentCandidate(
        criteria_id="CRIT_WAR",
        is_matched=True,
        alignment_score=0.85,
        node_id="b_war",
        section_path="第8章 维保方案",
        page_number=60,
        matched_quote="我方提供免费质保期 2 年，设立驻场保障团队。",
    )

    res = classifier.classify(item, candidate)
    assert res.deviation_type == DeviationType.FULL_COMPLIANCE
    assert res.severity == SeverityLevel.LOW
    assert res.score_assigned == 10.0
    assert res.confidence >= 0.95
    assert res.source_page == 60


def test_4_category_deviation_positive():
    """验证标书指标实质性优于招标要求 (POSITIVE 正偏离)"""
    classifier = FourCategoryDeviationClassifier()

    # 1. 越小越优型: 工期 330天 < 360天
    item_dur = TenderScoringItem(
        criteria_id="CRIT_POS_1",
        category=ScoringCategory.TECHNICAL,
        name="施工总工期",
        max_score=15.0,
        scoring_guide="工期不超过 360 天",
        constraint=CriteriaConstraint(
            metric_name="工期",
            target_value=360.0,
            target_unit="天",
            direction=MetricDirection.LOWER_BETTER,
        ),
    )
    cand_dur = AlignmentCandidate(
        criteria_id="CRIT_POS_1",
        is_matched=True,
        alignment_score=0.9,
        node_id="n1",
        section_path="第3章 进度计划",
        page_number=18,
        matched_quote="我公司郑重承诺本工程施工总工期为 330 日历天。",
    )
    res_dur = classifier.classify(item_dur, cand_dur)
    assert res_dur.deviation_type == DeviationType.POSITIVE
    assert res_dur.severity == SeverityLevel.INFO
    assert res_dur.score_assigned == 15.0
    assert res_dur.diff_payload["delta"] == -30.0

    # 2. 越大越优型: COP 5.6 > 5.0
    item_cop = TenderScoringItem(
        criteria_id="CRIT_POS_2",
        category=ScoringCategory.TECHNICAL,
        name="离心机组性能系数 COP",
        max_score=10.0,
        scoring_guide="COP 不低于 5.0",
        constraint=CriteriaConstraint(
            metric_name="COP",
            target_value=5.0,
            target_unit="",
            direction=MetricDirection.HIGHER_BETTER,
        ),
    )
    cand_cop = AlignmentCandidate(
        criteria_id="CRIT_POS_2",
        is_matched=True,
        alignment_score=0.92,
        node_id="n2",
        section_path="第5章 暖通节能",
        page_number=45,
        matched_quote="机组在额定工况下经第三方实测能效比 COP 达到 5.6。",
    )
    res_cop = classifier.classify(item_cop, cand_cop)
    assert res_cop.deviation_type == DeviationType.POSITIVE
    assert res_cop.severity == SeverityLevel.INFO
    assert res_cop.score_assigned == 10.0


def test_4_category_deviation_negative_critical():
    """验证标书指标劣于招标要求或包含顺延免责声明 (NEGATIVE 负偏离与废标预警)"""
    classifier = FourCategoryDeviationClassifier()

    # 1. 数值负偏离: 强制工期 <= 360天，标书承诺 400天
    item_kill = TenderScoringItem(
        criteria_id="CRIT_KILL",
        category=ScoringCategory.TECHNICAL,
        name="施工总工期",
        max_score=20.0,
        is_mandatory=True,
        scoring_guide="★ 施工总工期不得大于 360 天，超过作废标处理",
        constraint=CriteriaConstraint(
            metric_name="工期",
            target_value=360.0,
            target_unit="天",
            direction=MetricDirection.LOWER_BETTER,
        ),
    )
    cand_kill = AlignmentCandidate(
        criteria_id="CRIT_KILL",
        is_matched=True,
        alignment_score=0.88,
        node_id="n_dur_bad",
        section_path="第3章 工期规划",
        page_number=22,
        matched_quote="根据类似工程综合施工强度核算，本项目计划工期 400 日历天。",
    )
    res_kill = classifier.classify(item_kill, cand_kill)
    assert res_kill.deviation_type == DeviationType.NEGATIVE
    assert res_kill.severity == SeverityLevel.CRITICAL
    assert res_kill.score_assigned == 0.0
    assert "废标风险" in res_kill.description

    # 2. 定性免责负偏离: 标书附加排除责任条款
    item_qual = TenderScoringItem(
        criteria_id="CRIT_QUAL",
        category=ScoringCategory.TECHNICAL,
        name="地下管线与周边建筑保护方案",
        max_score=10.0,
        is_mandatory=False,
        scoring_guide="严格保护周边管线，承担全部因施工导致管线沉降的修复责任",
    )
    cand_hedge = AlignmentCandidate(
        criteria_id="CRIT_QUAL",
        is_matched=True,
        alignment_score=0.80,
        node_id="n_hedge",
        section_path="第6章 管线保护",
        page_number=35,
        matched_quote="如因地质暗沉或暴雨引发管线破裂，我司暂不包含相关修复费用，由招标人承担。",
    )
    res_hedge = classifier.classify(item_qual, cand_hedge)
    assert res_hedge.deviation_type == DeviationType.NEGATIVE
    assert res_hedge.severity == SeverityLevel.HIGH
    assert res_hedge.score_assigned < item_qual.max_score


def test_4_category_deviation_missing():
    """验证标书未响应项判定 (MISSING)"""
    classifier = FourCategoryDeviationClassifier()

    item_missing = TenderScoringItem(
        criteria_id="CRIT_UNMATCH",
        category=ScoringCategory.QUALIFICATION,
        name="BIM 技术应用成熟度与建模等级",
        max_score=5.0,
        is_mandatory=False,
        scoring_guide="需提供 LOD400 级别 BIM 模型与全生命周期协同平台",
    )
    candidate_empty = AlignmentCandidate(
        criteria_id="CRIT_UNMATCH",
        is_matched=False,
        alignment_score=0.1,
    )

    res_miss = classifier.classify(item_missing, candidate_empty)
    assert res_miss.deviation_type == DeviationType.MISSING
    assert res_miss.severity == SeverityLevel.HIGH
    assert res_miss.score_assigned == 0.0
    assert res_miss.confidence >= 0.90


# ===========================================================================
# 4. TenderAlignmentEngine 综合流程与 ReviewResult 实体映射测试
# ===========================================================================

def test_tender_alignment_engine_end_to_end():
    """验证端到端招投标偏离度比对、总分核算及持久化 ReviewResult 导出"""
    rfp_rows = [
        ["序号", "分类", "评分项", "分值", "评分细则"],
        ["1", "技术标", "施工总工期", "15分", "★ 工期不超过 360 天"],
        ["2", "技术标", "冷水机组 COP", "15分", "COP 不低于 5.0"],
        ["3", "商务标", "类似业绩数量", "10分", "同类业绩不少于 2 项"],
        ["4", "技术标", "智慧工地管理系统", "10分", "配备 AI 违规行为视频抓拍与实时预警"],
    ]

    rfp_ast = UnifiedDocumentAST(
        document_id="rfp_final_01",
        tenant_id="tenant_power",
        file_name="招标招标文件.pdf",
        source_type=DocumentSourceType.PDF,
        nodes=[
            ASTNode(
                block_id="rfp_t1",
                block_type=ASTBlockType.TABLE,
                section_path=["评标办法"],
                text_content="评分表",
                page_or_sheet="10",
                table_data=TableData(headers=[rfp_rows[0]], rows=rfp_rows[1:]),
            )
        ],
    )

    proposal_ast = UnifiedDocumentAST(
        document_id="proposal_final_01",
        tenant_id="tenant_power",
        file_name="自编投标文件.docx",
        source_type=DocumentSourceType.DOCX,
        nodes=[
            ASTNode(
                block_id="p_sec1",
                block_type=ASTBlockType.PARAGRAPH,
                section_path=["第3章 工期计划"],
                text_content="响应工期为 330 天（提前 30 天完成交付）。", # POSITIVE
                page_or_sheet="15",
            ),
            ASTNode(
                block_id="p_sec2",
                block_type=ASTBlockType.PARAGRAPH,
                section_path=["第5章 暖通设备"],
                text_content="选用高能效冷机，额定 COP 达到 5.0。", # FULL_COMPLIANCE
                page_or_sheet="28",
            ),
            # 类似业绩缺失 -> MISSING
            ASTNode(
                block_id="p_sec3",
                block_type=ASTBlockType.PARAGRAPH,
                section_path=["第9章 智慧工地"],
                text_content="我司建立智慧工地系统，但 AI 视频分析服务器费用另行收取。", # NEGATIVE (排他费用)
                page_or_sheet="60",
            ),
        ],
    )

    engine = TenderAlignmentEngine()
    report = engine.align_and_evaluate(rfp_ast, proposal_ast)

    assert report.total_criteria_count == 4
    assert report.positive_count == 1
    assert report.full_compliance_count == 1
    assert report.negative_count == 1
    assert report.missing_count == 1
    assert report.total_estimated_score > 0
    assert report.compliance_rate == 50.0  # (1 + 1) / 4 * 100

    # 导出并检验 ReviewResult ORM 实体
    entities = engine.to_review_results(report, task_id="task_eval_999")
    assert len(entities) == 4
    assert all(isinstance(e, ReviewResult) for e in entities)
    assert all(e.task_id == "task_eval_999" for e in entities)
    assert all(e.tenant_id == "tenant_power" for e in entities)

    dev_types = {e.deviation_type for e in entities}
    assert DeviationType.POSITIVE in dev_types
    assert DeviationType.FULL_COMPLIANCE in dev_types
    assert DeviationType.NEGATIVE in dev_types
    assert DeviationType.MISSING in dev_types

"""
文档大纲与排版格式质检引擎单元测试 (Outline Validator & Format QC Tests)
覆盖 Features 15 & 16:
1. 多编号体系双向解析 (NumberingParser)
2. 大纲层级跳跃与序号断层 100% 检出 (OutlineValidator)
3. 表格对齐、空单元格率、表头未合并异常、列表断层、题注连续性 (FormatValidator)
4. 综合质检报告与 ReviewResult 实体映射 (DocumentQualityEngine)
"""

import pytest
from app.models.audit_rag import DeviationType, ReviewResult, SeverityLevel
from app.quality.outline_validator import (
    DocumentQualityEngine,
    FormatIssueType,
    FormatValidator,
    HeadingNumberInfo,
    NumberingFamily,
    NumberingParser,
    OutlineIssueType,
    OutlineValidator,
    OutlineValidatorConfig,
)
from app.schemas.ast import (
    ASTBlockType,
    ASTNode,
    DocumentSourceType,
    TableCell,
    TableData,
    UnifiedDocumentAST,
)


# ===========================================================================
# 1. NumberingParser 编号解析与双向转换测试
# ===========================================================================

def test_numbering_parser_chinese_numerals():
    """验证中文大/小写数字与整数双向互转"""
    assert NumberingParser.parse_chinese_numeral("一") == 1
    assert NumberingParser.parse_chinese_numeral("十二") == 12
    assert NumberingParser.parse_chinese_numeral("二十三") == 23
    assert NumberingParser.parse_chinese_numeral("一百零五") == 105
    assert NumberingParser.parse_chinese_numeral("一千二百三十四") == 1234
    assert NumberingParser.parse_chinese_numeral("壹佰贰拾") == 120
    assert NumberingParser.parse_chinese_numeral("3") == 3

    assert NumberingParser.format_chinese_numeral(1) == "一"
    assert NumberingParser.format_chinese_numeral(12) == "十二"
    assert NumberingParser.format_chinese_numeral(23) == "二十三"
    assert NumberingParser.format_chinese_numeral(105) == "一百零五"
    assert NumberingParser.format_chinese_numeral(120) == "一百二十"


def test_numbering_parser_roman_numerals():
    """验证罗马数字双向互转"""
    assert NumberingParser.parse_roman_numeral("I") == 1
    assert NumberingParser.parse_roman_numeral("IV") == 4
    assert NumberingParser.parse_roman_numeral("IX") == 9
    assert NumberingParser.parse_roman_numeral("XIV") == 14
    assert NumberingParser.parse_roman_numeral("XLII") == 42
    assert NumberingParser.parse_roman_numeral("MCMXCIV") == 1994

    assert NumberingParser.format_roman_numeral(1) == "I"
    assert NumberingParser.format_roman_numeral(4) == "IV"
    assert NumberingParser.format_roman_numeral(14) == "XIV"
    assert NumberingParser.format_roman_numeral(42) == "XLII"


def test_numbering_parser_extract_heading_info():
    """验证多编号体系提取与族系识别"""
    # 点分十进制
    info = NumberingParser.extract_heading_info("1.2.3 关键技术方案")
    assert info is not None
    assert info.family == NumberingFamily.DECIMAL_DOT
    assert info.sequence_tuple == (1, 2, 3)
    assert info.clean_title == "关键技术方案"

    # 中文章节
    info = NumberingParser.extract_heading_info("第一章 项目总体规划与部署")
    assert info is not None
    assert info.family == NumberingFamily.CHINESE_CHAPTER
    assert info.sequence_tuple == (1,)
    assert info.clean_title == "项目总体规划与部署"

    # 中文顿号
    info = NumberingParser.extract_heading_info("三、 施工进度计划")
    assert info is not None
    assert info.family == NumberingFamily.CHINESE_IDEOGRAPHIC
    assert info.sequence_tuple == (3,)

    # 中文括号
    info = NumberingParser.extract_heading_info("（二） 质量保障措施")
    assert info is not None
    assert info.family == NumberingFamily.CHINESE_PARENTHESIZED
    assert info.sequence_tuple == (2,)

    # 阿拉伯单数点
    info = NumberingParser.extract_heading_info("5. 环保与水土保持")
    assert info is not None
    assert info.family == NumberingFamily.ARABIC_DOT
    assert info.sequence_tuple == (5,)

    # 罗马数字
    info = NumberingParser.extract_heading_info("IV. 附录与支撑材料")
    assert info is not None
    assert info.family == NumberingFamily.ROMAN
    assert info.sequence_tuple == (4,)


# ===========================================================================
# 2. OutlineValidator 大纲断层质检测试 (Feature 15)
# ===========================================================================

def test_outline_level_jump_detection():
    """验证 1级 直接跃升至 3级 的断层 100% 检出"""
    ast = UnifiedDocumentAST(
        document_id="doc_jump_001",
        tenant_id="tenant_test",
        file_name="level_jump.docx",
        source_type=DocumentSourceType.DOCX,
        nodes=[
            ASTNode(block_id="h1", block_type=ASTBlockType.HEADING, level=1, section_path=["第一章"], text_content="第一章 编制说明"),
            ASTNode(block_id="h2", block_type=ASTBlockType.HEADING, level=3, section_path=["第一章", "1.1.1"], text_content="1.1.1 编制依据"),
        ]
    )
    validator = OutlineValidator()
    report = validator.validate(ast)

    assert not report.is_valid
    jump_issues = [iss for iss in report.issues if iss.issue_type == OutlineIssueType.LEVEL_JUMP]
    assert len(jump_issues) == 1
    assert jump_issues[0].current_level == 3
    assert jump_issues[0].expected_level == 2
    assert jump_issues[0].severity == SeverityLevel.HIGH


def test_outline_sequence_gap_decimal():
    """验证 1.1 跳跃至 1.3 缺失 1.2 及 2.1.1 跳跃至 2.1.3 100% 检出"""
    ast = UnifiedDocumentAST(
        document_id="doc_gap_001",
        tenant_id="tenant_test",
        file_name="decimal_gap.docx",
        source_type=DocumentSourceType.DOCX,
        nodes=[
            ASTNode(block_id="h1", block_type=ASTBlockType.HEADING, level=1, section_path=["1"], text_content="1 工程概况"),
            ASTNode(block_id="h2", block_type=ASTBlockType.HEADING, level=2, section_path=["1", "1.1"], text_content="1.1 建设地点"),
            # 跳过 1.2，直接出现 1.3
            ASTNode(block_id="h3", block_type=ASTBlockType.HEADING, level=2, section_path=["1", "1.3"], text_content="1.3 建设规模"),
            ASTNode(block_id="h4", block_type=ASTBlockType.HEADING, level=1, section_path=["2"], text_content="2 施工组织设计"),
            ASTNode(block_id="h5", block_type=ASTBlockType.HEADING, level=2, section_path=["2", "2.1"], text_content="2.1 组织机构"),
            ASTNode(block_id="h6", block_type=ASTBlockType.HEADING, level=3, section_path=["2", "2.1", "2.1.1"], text_content="2.1.1 项目经理部"),
            # 跳过 2.1.2，直接出现 2.1.3
            ASTNode(block_id="h7", block_type=ASTBlockType.HEADING, level=3, section_path=["2", "2.1", "2.1.3"], text_content="2.1.3 技术负责人职责"),
        ]
    )
    validator = OutlineValidator()
    report = validator.validate(ast)

    gaps = [iss for iss in report.issues if iss.issue_type == OutlineIssueType.SEQUENCE_GAP]
    assert len(gaps) >= 2

    # 验证检出 1.2 缺失
    gap_1 = next(g for g in gaps if "1.3" in g.current_heading)
    assert "1.2" in gap_1.missing_items

    # 验证检出 2.1.2 缺失
    gap_2 = next(g for g in gaps if "2.1.3" in g.current_heading)
    assert "2.1.2" in gap_2.missing_items


def test_outline_sequence_gap_chinese():
    """验证第一章跳跃至第四章，精确指出缺失 第二章、第三章"""
    ast = UnifiedDocumentAST(
        document_id="doc_cn_gap",
        tenant_id="tenant_test",
        file_name="cn_gap.docx",
        source_type=DocumentSourceType.DOCX,
        nodes=[
            ASTNode(block_id="h1", block_type=ASTBlockType.HEADING, level=1, section_path=["第一章"], text_content="第一章 综合说明"),
            # 跳过第二章、第三章
            ASTNode(block_id="h2", block_type=ASTBlockType.HEADING, level=1, section_path=["第四章"], text_content="第四章 质量管理体系"),
        ]
    )
    validator = OutlineValidator()
    report = validator.validate(ast)

    gaps = [iss for iss in report.issues if iss.issue_type == OutlineIssueType.SEQUENCE_GAP]
    assert len(gaps) == 1
    assert "第二章" in gaps[0].missing_items
    assert "第三章" in gaps[0].missing_items


def test_outline_root_level_skip():
    """验证文档首标题直接以 2 级开头的根断层"""
    ast = UnifiedDocumentAST(
        document_id="doc_root_skip",
        tenant_id="tenant_test",
        file_name="root_skip.docx",
        source_type=DocumentSourceType.DOCX,
        nodes=[
            ASTNode(block_id="h1", block_type=ASTBlockType.HEADING, level=2, section_path=["1.1"], text_content="1.1 绪论"),
        ]
    )
    validator = OutlineValidator()
    report = validator.validate(ast)

    root_issues = [iss for iss in report.issues if iss.issue_type == OutlineIssueType.ROOT_LEVEL_SKIP]
    assert len(root_issues) == 1
    assert root_issues[0].current_level == 2


def test_outline_duplicate_and_out_of_order():
    """验证同级重复序号与倒序序号"""
    ast = UnifiedDocumentAST(
        document_id="doc_dup",
        tenant_id="tenant_test",
        file_name="dup.docx",
        source_type=DocumentSourceType.DOCX,
        nodes=[
            ASTNode(block_id="h1", block_type=ASTBlockType.HEADING, level=1, section_path=["1"], text_content="1.1 前言"),
            ASTNode(block_id="h2", block_type=ASTBlockType.HEADING, level=1, section_path=["1"], text_content="1.1 重复的前言"),
            ASTNode(block_id="h3", block_type=ASTBlockType.HEADING, level=1, section_path=["1"], text_content="1.0 倒序"),
        ]
    )
    validator = OutlineValidator()
    report = validator.validate(ast)

    dup_issues = [iss for iss in report.issues if iss.issue_type == OutlineIssueType.DUPLICATE_NUMBER]
    assert len(dup_issues) >= 1

    order_issues = [iss for iss in report.issues if iss.issue_type == OutlineIssueType.OUT_OF_ORDER]
    assert len(order_issues) >= 1


# ===========================================================================
# 3. FormatValidator 排版与表格质检测试 (Feature 16)
# ===========================================================================

def test_format_table_empty_cell_ratio():
    """验证表格空单元格比例超过 30% 告警"""
    # 5行4列，总共20个单元格，其中12个为空 (60%)
    rows = [
        ["Item1", "", "", ""],
        ["Item2", "100", "", ""],
        ["", "", "", ""],
        ["Item4", "", "OK", ""],
        ["Item5", "", "", ""],
    ]
    ast = UnifiedDocumentAST(
        document_id="doc_tbl_empty",
        tenant_id="tenant_test",
        file_name="table_empty.docx",
        source_type=DocumentSourceType.DOCX,
        nodes=[
            ASTNode(
                block_id="t1",
                block_type=ASTBlockType.TABLE,
                section_path=["第3章"],
                text_content="表格数据",
                table_data=TableData(headers=[["Col1", "Col2", "Col3", "Col4"]], rows=rows),
            )
        ]
    )
    validator = FormatValidator()
    report = validator.validate(ast)

    ratio_issues = [iss for iss in report.issues if iss.issue_type == FormatIssueType.TABLE_EMPTY_CELL_RATIO_HIGH]
    assert len(ratio_issues) == 1
    assert ratio_issues[0].metric_value is not None
    assert ratio_issues[0].metric_value >= 0.30


def test_format_table_column_mismatch():
    """验证表格各行列数不对齐网格破坏告警"""
    rows = [
        ["A", "B", "C"],
        ["A", "B"],        # 缺少一列
        ["A", "B", "C"],
    ]
    ast = UnifiedDocumentAST(
        document_id="doc_tbl_mismatch",
        tenant_id="tenant_test",
        file_name="table_mismatch.docx",
        source_type=DocumentSourceType.DOCX,
        nodes=[
            ASTNode(
                block_id="t1",
                block_type=ASTBlockType.TABLE,
                section_path=["第3章"],
                text_content="表格",
                table_data=TableData(headers=[["C1", "C2", "C3"]], rows=rows),
            )
        ]
    )
    validator = FormatValidator()
    report = validator.validate(ast)

    mismatch_issues = [iss for iss in report.issues if iss.issue_type == FormatIssueType.TABLE_COLUMN_MISMATCH]
    assert len(mismatch_issues) >= 1


def test_format_captions_and_orphan_references():
    """验证图表题注断层与正文悬挂图件引用"""
    ast = UnifiedDocumentAST(
        document_id="doc_caption",
        tenant_id="tenant_test",
        file_name="caption.docx",
        source_type=DocumentSourceType.DOCX,
        nodes=[
            ASTNode(block_id="p1", block_type=ASTBlockType.PARAGRAPH, section_path=["第1章"], text_content="表 1-1 项目主要经济技术指标"),
            # 表 1-2 缺失，直接跃升至表 1-3
            ASTNode(block_id="p2", block_type=ASTBlockType.PARAGRAPH, section_path=["第1章"], text_content="表 1-3 建设进度节点控制表"),
            # 正文引用了不存在的图件 图 2-5
            ASTNode(block_id="p3", block_type=ASTBlockType.PARAGRAPH, section_path=["第1章"], text_content="总体布置平面关系参见附图 2-5，各分区独立管理。"),
        ]
    )
    validator = FormatValidator()
    report = validator.validate(ast)

    gap_issues = [iss for iss in report.issues if iss.issue_type == FormatIssueType.TABLE_CAPTION_SEQUENCE_GAP]
    assert len(gap_issues) >= 1
    assert "表 1-2" in gap_issues[0].message or "1-2" in gap_issues[0].message

    orphan_issues = [iss for iss in report.issues if iss.issue_type == FormatIssueType.ORPHAN_FIGURE_REFERENCE]
    assert len(orphan_issues) >= 1
    assert "2-5" in orphan_issues[0].message


def test_format_broken_and_truncated_lists():
    """验证正文列表序号断层与文本异常截断"""
    ast = UnifiedDocumentAST(
        document_id="doc_lists",
        tenant_id="tenant_test",
        file_name="lists.docx",
        source_type=DocumentSourceType.DOCX,
        nodes=[
            ASTNode(block_id="p1", block_type=ASTBlockType.PARAGRAPH, section_path=["第1章"], text_content="（1） 严格落实安全生产第一责任人职责；"),
            # （2）缺失
            ASTNode(block_id="p2", block_type=ASTBlockType.PARAGRAPH, section_path=["第1章"], text_content="（3） 定期开展特种作业人员考核，"), # 截断结尾
        ]
    )
    validator = FormatValidator()
    report = validator.validate(ast)

    broken_issues = [iss for iss in report.issues if iss.issue_type == FormatIssueType.BROKEN_LIST_SEQUENCE]
    assert len(broken_issues) >= 1

    trunc_issues = [iss for iss in report.issues if iss.issue_type == FormatIssueType.TRUNCATED_LIST_ITEM]
    assert len(trunc_issues) >= 1


# ===========================================================================
# 4. DocumentQualityEngine 综合门面与 ReviewResult 桥接测试
# ===========================================================================

def test_document_quality_engine_full_workflow():
    """验证统一质检总入口、健康评分及与 SQLAlchemy 实体映射"""
    ast = UnifiedDocumentAST(
        document_id="doc_full_001",
        tenant_id="tenant_alpha",
        file_name="full_quality_sample.docx",
        source_type=DocumentSourceType.DOCX,
        nodes=[
            ASTNode(block_id="h1", block_type=ASTBlockType.HEADING, level=1, section_path=["第一章"], text_content="第一章 综合说明"),
            ASTNode(block_id="h2", block_type=ASTBlockType.HEADING, level=2, section_path=["第一章", "1.1"], text_content="1.1 总体目标"),
            ASTNode(block_id="h3", block_type=ASTBlockType.HEADING, level=2, section_path=["第一章", "1.3"], text_content="1.3 实施范围"),
            ASTNode(
                block_id="t1",
                block_type=ASTBlockType.TABLE,
                section_path=["第一章"],
                text_content="空表格",
                table_data=TableData(headers=[["A", "B"]], rows=[["", ""], ["", ""]]),
            ),
        ]
    )
    engine = DocumentQualityEngine()
    report = engine.validate_document(ast)

    assert report.total_issues_count > 0
    assert report.overall_score < 100.0
    assert not report.passed

    # 导出为数据库持久化 ReviewResult 列表
    review_entities = engine.to_review_results(report, task_id="task_qc_123", tenant_id="tenant_alpha")
    assert len(review_entities) == report.total_issues_count
    assert all(isinstance(r, ReviewResult) for r in review_entities)
    assert all(r.tenant_id == "tenant_alpha" for r in review_entities)
    assert any(r.deviation_type == DeviationType.NEGATIVE for r in review_entities)

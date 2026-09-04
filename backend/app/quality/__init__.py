"""
质量与一致性审查引擎模块 (Quality & Consistency Auditing Engines)
对齐 Phase 2 (M2) 核心特性 (Features 15-22):
- Feature 15 & 16: 大纲层级与序号断层质检、排版与表格结构校验 (OutlineValidator, FormatValidator, DocumentQualityEngine)
- Feature 17, 18, 19: 长文档跨章节数值一致性校验、量纲自适应归一化换算与全证据链冲突图谱 (ConsistencyEngine, MetricNormalizer)
- Feature 20, 21, 22: 招投标评分表结构化拆解、自编标书语义对齐与 4 类偏离度分类矩阵 (RFPScoringTableParser, BidSemanticAlignmentEngine, FourCategoryDeviationClassifier, TenderAlignmentEngine)
"""

from app.quality.consistency_engine import (
    ConflictType,
    ConsistencyConflict,
    ConsistencyEngine,
    ConsistencyReport,
    IssueSeverity,
    MetricDimension,
    MetricExtractionRule,
    MetricNormalizer,
    StatementAnchor,
)
from app.quality.outline_validator import (
    DocumentQualityEngine,
    FormatIssue,
    FormatIssueType,
    FormatValidationReport,
    FormatValidator,
    HeadingNumberInfo,
    NumberingFamily,
    NumberingParser,
    OutlineIssue,
    OutlineIssueType,
    OutlineValidationReport,
    OutlineValidator,
    OutlineValidatorConfig,
)
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

__all__ = [
    # Features 15 & 16
    "OutlineIssueType",
    "FormatIssueType",
    "NumberingFamily",
    "OutlineValidatorConfig",
    "OutlineIssue",
    "FormatIssue",
    "OutlineValidationReport",
    "FormatValidationReport",
    "HeadingNumberInfo",
    "NumberingParser",
    "OutlineValidator",
    "FormatValidator",
    "DocumentQualityEngine",
    # Features 17, 18, 19
    "ConflictType",
    "IssueSeverity",
    "MetricDimension",
    "StatementAnchor",
    "ConsistencyConflict",
    "ConsistencyReport",
    "MetricNormalizer",
    "MetricExtractionRule",
    "ConsistencyEngine",
    # Features 20, 21, 22
    "ScoringCategory",
    "MetricDirection",
    "CriteriaConstraint",
    "TenderScoringItem",
    "TenderScoringTable",
    "AlignmentCandidate",
    "DeviationEvaluationResult",
    "TenderAlignmentReport",
    "RFPScoringTableParser",
    "BidSemanticAlignmentEngine",
    "FourCategoryDeviationClassifier",
    "TenderAlignmentEngine",
]

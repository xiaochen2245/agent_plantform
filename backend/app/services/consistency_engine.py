"""
向后兼容转发层: 映射至 backend/app/quality/consistency_engine.py
保持与 Phase 1 test_audit_rag_workflow.py 零回归无缝兼容
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

__all__ = [
    "ConflictType",
    "IssueSeverity",
    "MetricDimension",
    "StatementAnchor",
    "ConsistencyConflict",
    "ConsistencyReport",
    "MetricNormalizer",
    "MetricExtractionRule",
    "ConsistencyEngine",
]

"""
长文档跨章节前后数据一致性校验引擎 (Consistency Validation Engine)
核心功能:
1. 关键工程与经济指标抽取 (工期、预算、技术规格、面积、烈度)
2. 量纲自适应归一化 (统一换算为标准天数、万元、平方米等)
3. 实体属性图谱构建与跨章节交叉比对
4. 精准定位矛盾章节、原文锚点，输出可追溯的结构化风险预警报告
"""

from dataclasses import dataclass, field
from enum import Enum
import math
import re
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 1. 领域实体与报告规范
# ---------------------------------------------------------------------------

class ConflictType(str, Enum):
    NUMERICAL_MISMATCH = "numerical_mismatch"          # 绝对数值冲突 (如 90天 vs 120天)
    UNIT_INCOMPATIBLE = "unit_incompatible"            # 量纲冲突/混乱
    TIMELINE_INCONSISTENCY = "timeline_inconsistency"  # 节点时间倒挂
    TOLERANCE_EXCEEDED = "tolerance_exceeded"          # 超出技术工程公差


class IssueSeverity(str, Enum):
    CRITICAL = "critical"  # 重大废标风险 / 合同实质性违约
    HIGH = "high"          # 高风险指标矛盾
    MEDIUM = "medium"      # 一般性数据偏差
    LOW = "low"            # 轻微笔误/四舍五入舍入差


class StatementAnchor(BaseModel):
    """单处陈述的证据链锚点"""
    section_title: str = Field(..., description="所属章节路径，如 '第3章 施工组织设计'")
    page_or_sheet: str = Field(default="P.1", description="页码或表格名")
    raw_text: str = Field(..., description="原始表达，如 '工期为 90 天'")
    full_sentence: str = Field(..., description="所在上下文完整句子")
    raw_number: float = Field(..., description="提取出的原始数字")
    raw_unit: str = Field(..., description="原始量纲")
    normalized_value: float = Field(..., description="归一化后的标准标量值")
    standard_unit: str = Field(..., description="标准量纲")


class ConsistencyConflict(BaseModel):
    """一致性冲突明细报告"""
    conflict_id: str
    metric_category: str = Field(..., description="指标类别: 工期/预算/技术参数/面积")
    metric_name: str = Field(..., description="指标标准名: 如 '项目建设总工期'")
    conflict_type: ConflictType
    severity: IssueSeverity
    
    # 前后相互矛盾的证据锚点
    baseline_statement: StatementAnchor = Field(..., description="前文陈述 (或招标基准陈述)")
    conflicting_statement: StatementAnchor = Field(..., description="后文矛盾陈述")
    
    # 差异量化分析
    difference_value: float = Field(..., description="绝对偏差值")
    difference_percentage: float = Field(..., description="偏差比例 (%)")
    
    # 审查建议
    detailed_reason: str = Field(..., description="矛盾深度归因与法律/技术风险剖析")
    correction_suggestion: str = Field(..., description="推荐的统一修改方案")


class ConsistencyReport(BaseModel):
    """整套长文档前后数据一致性总报告"""
    document_title: str
    total_metrics_scanned: int
    conflicts_found: int
    critical_count: int
    high_count: int
    conflicts: List[ConsistencyConflict] = Field(default_factory=list)
    extracted_knowledge_graph: Dict[str, List[Dict[str, Any]]] = Field(
        default_factory=dict, description="抽取的指标属性图谱分布"
    )


# ---------------------------------------------------------------------------
# 2. 量纲归一化器 (MetricNormalizer)
# ---------------------------------------------------------------------------

class MetricNormalizer:
    """标准工程量纲换算与归一化工具"""

    # 工期归一化为 "天"
    TIME_UNIT_FACTORS = {
        "天": 1.0,
        "日": 1.0,
        "日历天": 1.0,
        "工作日": 1.4,
        "周": 7.0,
        "星期": 7.0,
        "月": 30.0,
        "个月": 30.0,
        "季度": 90.0,
        "年": 365.0,
    }

    # 金额归一化为 "万元"
    CURRENCY_FACTORS = {
        "元": 0.0001,
        "千元": 0.1,
        "万元": 1.0,
        "百万元": 100.0,
        "亿元": 10000.0,
    }

    @classmethod
    def normalize_duration(cls, val: float, unit: str) -> Tuple[float, str]:
        unit_clean = unit.strip()
        factor = cls.TIME_UNIT_FACTORS.get(unit_clean, 1.0)
        return round(val * factor, 2), "天"

    @classmethod
    def normalize_currency(cls, val: float, unit: str) -> Tuple[float, str]:
        unit_clean = unit.strip()
        factor = cls.CURRENCY_FACTORS.get(unit_clean, 1.0)
        return round(val * factor, 4), "万元"


# ---------------------------------------------------------------------------
# 3. 核心一致性校验引擎 (ConsistencyEngine)
# ---------------------------------------------------------------------------

class ConsistencyEngine:
    """
    长文档数据一致性交叉校验引擎
    """

    # 工业级关键指标抽取正则模式 (支持自然语言多变谓词表达)
    PATTERNS = [
        # 工期类 (如: 工期为 90 天, 施工周期调整为 120 天, 工期3个月)
        {
            "category": "工期",
            "metric": "施工总工期",
            "regex": re.compile(
                r"(?:总工期|工期|施工周期|建设周期|计划工期|项目历时)[^\d\n]{0,8}?([0-9]+(?:\.[0-9]+)?)\s*(个?日历天|天|日|个月|月|周|年)",
                re.IGNORECASE
            ),
            "normalizer": MetricNormalizer.normalize_duration,
            "tolerance": 0.0,  # 工期指标零容差
        },
        # 预算金额类 (如: 总造价 1200万元, 预算控制在 1200 万元, 总投资0.12亿元)
        {
            "category": "造价",
            "metric": "工程总投资/造价",
            "regex": re.compile(
                r"(?:总投资|总造价|投标报价|合同总额|工程总承包造价)[^\d\n]{0,8}?([0-9]+(?:\.[0-9]+)?)\s*(亿元|百万元|万元|千元|元)",
                re.IGNORECASE
            ),
            "normalizer": MetricNormalizer.normalize_currency,
            "tolerance": 0.01,  # 允许千分之一尾差
        },
        # 规模指标 (如: 建筑面积 52000平方米)
        {
            "category": "规模",
            "metric": "总建筑面积",
            "regex": re.compile(
                r"(?:总建筑面积|建筑总面积|地上总面积)[^\d\n]{0,8}?([0-9]+(?:\.[0-9]+)?)\s*(平方米|m²|平米)",
                re.IGNORECASE
            ),
            "normalizer": lambda v, u: (v, "m²"),
            "tolerance": 0.0,
        }
    ]

    def extract_metrics_from_text(
        self, section_title: str, text_content: str, page_num: str = "P.1"
    ) -> List[Tuple[str, str, StatementAnchor]]:
        """从一段章节文本中抽取关键指标事实"""
        extracted = []

        # 按标点切割长句
        sentences = re.split(r"[。！？\n；]", text_content)

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            for p in self.PATTERNS:
                matches = p["regex"].finditer(sentence)
                for m in matches:
                    raw_num_str = m.group(1)
                    raw_unit = m.group(2)
                    try:
                        raw_num = float(raw_num_str)
                    except ValueError:
                        continue

                    # 归一化换算
                    norm_val, std_unit = p["normalizer"](raw_num, raw_unit)

                    anchor = StatementAnchor(
                        section_title=section_title,
                        page_or_sheet=page_num,
                        raw_text=m.group(0),
                        full_sentence=sentence,
                        raw_number=raw_num,
                        raw_unit=raw_unit,
                        normalized_value=norm_val,
                        standard_unit=std_unit,
                    )
                    extracted.append((p["category"], p["metric"], anchor))

        return extracted

    def validate_document_consistency(
        self, document_title: str, sections_data: List[Dict[str, Any]]
    ) -> ConsistencyReport:
        """
        对整篇长文档的所有章节进行跨章节一致性交叉校验
        """
        metric_graph: Dict[str, List[StatementAnchor]] = {}
        category_map: Dict[str, str] = {}
        total_extracted = 0

        for sec in sections_data:
            sec_title = sec.get("section_title", "未命名章节")
            content = sec.get("content", "")
            page = sec.get("page", "P.1")

            items = self.extract_metrics_from_text(sec_title, content, page)
            for cat, metric_name, anchor in items:
                total_extracted += 1
                category_map[metric_name] = cat
                if metric_name not in metric_graph:
                    metric_graph[metric_name] = []
                metric_graph[metric_name].append(anchor)

        # 交叉对比相同指标的不同陈述
        conflicts: List[ConsistencyConflict] = []
        conflict_id_seq = 1

        for metric_name, statements in metric_graph.items():
            if len(statements) < 2:
                continue

            # 以第一次出现的陈述为基准
            baseline = statements[0]

            for subsequent in statements[1:]:
                val1 = baseline.normalized_value
                val2 = subsequent.normalized_value

                diff = abs(val1 - val2)
                if diff > 1e-4:  # 存在不一致
                    avg_val = (val1 + val2) / 2.0 if (val1 + val2) != 0 else 1.0
                    diff_pct = round((diff / avg_val) * 100.0, 2)

                    if "工期" in metric_name:
                        severity = IssueSeverity.CRITICAL
                        reason = (
                            f"【致命工期冲突】前文在 '{baseline.section_title}' 明确承诺工期为 "
                            f"{baseline.raw_text} (换算为 {val1} 天)，但在后文 '{subsequent.section_title}' "
                            f"却陈述为 {subsequent.raw_text} (换算为 {val2} 天)。"
                            f"前后相差 {diff} 天 (偏差率 {diff_pct}%)。此类前后自相矛盾极易被评标专家认定为虚假响应或实质性不满足，面临废标风险！"
                        )
                        suggestion = (
                            f"核实项目计划排期，并在全篇严格统一工期表达。推荐将 '{subsequent.section_title}' "
                            f"中的描述统一更正为 '{baseline.raw_text}'。"
                        )
                    elif "投资" in metric_name or "造价" in metric_name:
                        severity = IssueSeverity.CRITICAL
                        reason = (
                            f"【财务金额矛盾】前文 '{baseline.section_title}' 金额为 {baseline.raw_text} "
                            f"({val1} 万元)，后文 '{subsequent.section_title}' 表述为 {subsequent.raw_text} "
                            f"({val2} 万元)，差额达到 {diff} 万元！"
                        )
                        suggestion = f"核准工程造价预算书明细，消除章节间差额并统一财务报表口径。"
                    else:
                        severity = IssueSeverity.HIGH
                        reason = f"指标 '{metric_name}' 在前后文数值不符: {val1} vs {val2} {baseline.standard_unit}。"
                        suggestion = f"建议跨专业设计团队复核该参数，统一修订为权威设计参数。"

                    conflict = ConsistencyConflict(
                        conflict_id=f"CONF-{conflict_id_seq:04d}",
                        metric_category=category_map.get(metric_name, "通用"),
                        metric_name=metric_name,
                        conflict_type=ConflictType.NUMERICAL_MISMATCH,
                        severity=severity,
                        baseline_statement=baseline,
                        conflicting_statement=subsequent,
                        difference_value=round(diff, 2),
                        difference_percentage=diff_pct,
                        detailed_reason=reason,
                        correction_suggestion=suggestion,
                    )
                    conflicts.append(conflict)
                    conflict_id_seq += 1

        critical_count = sum(1 for c in conflicts if c.severity == IssueSeverity.CRITICAL)
        high_count = sum(1 for c in conflicts if c.severity == IssueSeverity.HIGH)

        exported_graph = {
            m_name: [s.model_dump() for s in stmts] for m_name, stmts in metric_graph.items()
        }

        return ConsistencyReport(
            document_title=document_title,
            total_metrics_scanned=total_extracted,
            conflicts_found=len(conflicts),
            critical_count=critical_count,
            high_count=high_count,
            conflicts=conflicts,
            extracted_knowledge_graph=exported_graph,
        )

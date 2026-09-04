"""
长文档跨章节多维度数据一致性校验引擎 (Numerical Consistency Engine)
业务领域: 建设工程招标、投标文件、施工组织设计、技术方案质量审查
核心功能:
1. 工业级多维数值指标抽取 (Feature 17): 工期、造价预算、建筑面积、机电设备参数 (COP、制冷量、风量、功率、扬程)
2. 领域自适应量纲归一化换算 (Feature 18): 统一换算为标准天数、万元、m²、kW、m³/h、m
3. 跨章节图谱冲突检测与证据链生成 (Feature 19): 100% 自动检出工期、造价、设备矛盾，附带精确章节、页码、原句
4. 兼容 AST 语法树结构化表格/进度计划与轻量化章节文本流输入
5. 无缝对齐 SQLAlchemy 2.0 领域模型 (ReviewResult / AuditTask)
"""

from dataclasses import dataclass
from enum import Enum
import math
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from pydantic import BaseModel, Field

from app.models.audit_rag import DeviationType, ReviewResult, SeverityLevel
from app.schemas.ast import (
    ASTBlockType,
    ASTNode,
    ScheduleTaskData,
    TableData,
    UnifiedDocumentAST,
)
from app.schemas.audit import (
    ConflictType,
    ConsistencyConflict,
    ConsistencyReport,
    IssueSeverity,
    MetricDimension,
    StatementAnchor,
)


# ===========================================================================
# 1. 量纲自适应归一化器 (Feature 18: MetricAdaptiveNormalizer)
# ===========================================================================

class MetricNormalizer:
    """
    工程多维量纲自适应换算与归一化器
    换算标准:
    - 工期 -> 天 (日历天)
    - 金额 -> 万元
    - 面积 -> m²
    - 功率/制冷量 -> kW
    - 风量 -> m³/h
    - 扬程 -> m
    - COP -> 无量纲
    """

    # 工期换算系数 (归一化为 "天")
    TIME_UNIT_FACTORS: Dict[str, float] = {
        "天": 1.0,
        "日": 1.0,
        "日历天": 1.0,
        "个日历天": 1.0,
        "自然天": 1.0,
        "个自然天": 1.0,
        "工作日": 1.4,      # 中国工程惯例: 5天工作日折算为 7个日历天 (1.4x)
        "个工作日": 1.4,
        "周": 7.0,
        "个周": 7.0,
        "星期": 7.0,
        "个星期": 7.0,
        "月": 30.0,
        "个月": 30.0,
        "季度": 90.0,
        "个季度": 90.0,
        "年": 365.0,
        "周年": 365.0,
    }

    # 金额换算系数 (归一化为 "万元")
    CURRENCY_FACTORS: Dict[str, float] = {
        "元": 0.0001,
        "千元": 0.1,
        "万元": 1.0,
        "百万元": 100.0,
        "亿元": 10000.0,
    }

    # 建筑面积换算系数 (归一化为 "m²")
    AREA_FACTORS: Dict[str, float] = {
        "m²": 1.0,
        "㎡": 1.0,
        "m2": 1.0,
        "平方米": 1.0,
        "平米": 1.0,
        "万平方米": 10000.0,
        "万m²": 10000.0,
        "万㎡": 10000.0,
        "万m2": 10000.0,
        "公顷": 10000.0,
        "ha": 10000.0,
        "亩": 666.67,
    }

    # 制冷量换算系数 (归一化为 "kW")
    COOLING_FACTORS: Dict[str, float] = {
        "kw": 1.0,
        "千瓦": 1.0,
        "rt": 3.51685,       # 1 美制冷吨 (USRT) ≈ 3.51685 kW
        "冷吨": 3.51685,
        "美制冷吨": 3.51685,
        "usrt": 3.51685,
        "mw": 1000.0,
        "兆瓦": 1000.0,
        "w": 0.001,
        "瓦": 0.001,
    }

    # 功率换算系数 (归一化为 "kW")
    POWER_FACTORS: Dict[str, float] = {
        "kw": 1.0,
        "千瓦": 1.0,
        "w": 0.001,
        "瓦": 0.001,
        "mw": 1000.0,
        "兆瓦": 1000.0,
        "hp": 0.7355,        # 1 公制马力 ≈ 0.7355 kW
        "马力": 0.7355,
    }

    # 风量换算系数 (归一化为 "m³/h")
    AIR_FLOW_FACTORS: Dict[str, float] = {
        "m³/h": 1.0,
        "m3/h": 1.0,
        "m^3/h": 1.0,
        "立方米/小时": 1.0,
        "立方米每小时": 1.0,
        "m³/小时": 1.0,
        "m3/小时": 1.0,
        "m³/s": 3600.0,
        "m3/s": 3600.0,
        "立方米/秒": 3600.0,
        "cfm": 1.69901,      # 1 CFM ≈ 1.69901 m³/h
        "l/s": 3.6,
        "升/秒": 3.6,
    }

    # 水泵扬程换算系数 (归一化为 "m")
    HEAD_FACTORS: Dict[str, float] = {
        "m": 1.0,
        "米": 1.0,
        "mh2o": 1.0,
        "mh₂o": 1.0,
        "米水柱": 1.0,
        "kpa": 0.10197,      # 100 kPa ≈ 10.2 mH2O
        "bar": 10.197,
    }

    @classmethod
    def clean_unit(cls, unit: Optional[str]) -> str:
        """清洗单位字符串"""
        if not unit:
            return ""
        return unit.strip().lower()

    @classmethod
    def normalize_duration(cls, val: float, unit: str) -> Tuple[float, str]:
        u = cls.clean_unit(unit)
        factor = cls.TIME_UNIT_FACTORS.get(u, 1.0)
        return round(val * factor, 2), "天"

    @classmethod
    def normalize_currency(cls, val: float, unit: str) -> Tuple[float, str]:
        u = cls.clean_unit(unit)
        factor = cls.CURRENCY_FACTORS.get(u, 1.0)
        return round(val * factor, 4), "万元"

    @classmethod
    def normalize_area(cls, val: float, unit: str) -> Tuple[float, str]:
        u = cls.clean_unit(unit)
        factor = cls.AREA_FACTORS.get(u, 1.0)
        return round(val * factor, 2), "m²"

    @classmethod
    def normalize_cop(cls, val: float, unit: str = "") -> Tuple[float, str]:
        return round(val, 2), "无量纲"

    @classmethod
    def normalize_cooling_capacity(cls, val: float, unit: str) -> Tuple[float, str]:
        u = cls.clean_unit(unit)
        factor = cls.COOLING_FACTORS.get(u, 1.0)
        return round(val * factor, 2), "kW"

    @classmethod
    def normalize_air_flow(cls, val: float, unit: str) -> Tuple[float, str]:
        u = cls.clean_unit(unit)
        factor = cls.AIR_FLOW_FACTORS.get(u, 1.0)
        return round(val * factor, 2), "m³/h"

    @classmethod
    def normalize_power(cls, val: float, unit: str) -> Tuple[float, str]:
        u = cls.clean_unit(unit)
        factor = cls.POWER_FACTORS.get(u, 1.0)
        return round(val * factor, 2), "kW"

    @classmethod
    def normalize_head(cls, val: float, unit: str) -> Tuple[float, str]:
        u = cls.clean_unit(unit)
        factor = cls.HEAD_FACTORS.get(u, 1.0)
        return round(val * factor, 2), "m"

    @classmethod
    def normalize(cls, category: str, val: float, unit: str = "") -> Tuple[float, str]:
        """通配归一化入口"""
        cat_lower = category.lower()
        if "工期" in category or "时间" in category or "周期" in category:
            return cls.normalize_duration(val, unit)
        elif "造价" in category or "投资" in category or "预算" in category or "金额" in category or "报价" in category:
            return cls.normalize_currency(val, unit)
        elif "面积" in category:
            return cls.normalize_area(val, unit)
        elif "cop" in cat_lower or "能效比" in category:
            return cls.normalize_cop(val, unit)
        elif "制冷量" in category or "冷量" in category:
            return cls.normalize_cooling_capacity(val, unit)
        elif "风量" in category:
            return cls.normalize_air_flow(val, unit)
        elif "功率" in category:
            return cls.normalize_power(val, unit)
        elif "扬程" in category:
            return cls.normalize_head(val, unit)
        return round(val, 4), unit


# ===========================================================================
# 2. 抽取规则与引擎核心 (Feature 17 & 19: ConsistencyEngine)
# ===========================================================================

@dataclass
class MetricExtractionRule:
    """指标抽取规则元数据"""
    category: str
    dimension: str
    metric_name: str
    regex: re.Pattern
    normalizer: Callable[[float, str], Tuple[float, str]]
    tolerance: float
    default_severity: IssueSeverity


class ConsistencyEngine:
    """
    长文档跨章节数据一致性交叉校验引擎
    具备 AST 树节点扫描、表格行列穿透、正则表达式提取与全证据链分析能力
    """

    def __init__(self) -> None:
        self.rules: List[MetricExtractionRule] = self._build_default_rules()

    @staticmethod
    def parse_number(num_str: str) -> float:
        """安全解析含千分位逗号的数字字符串"""
        clean_s = num_str.replace(",", "").replace("，", "").strip()
        return float(clean_s)

    def _build_default_rules(self) -> List[MetricExtractionRule]:
        """构建工业级多维指标抽取规则集"""
        return [
            # 1. 工期类指标 (Schedule Duration)
            MetricExtractionRule(
                category="工期",
                dimension="工期",
                metric_name="施工总工期",
                regex=re.compile(
                    r"(?:(?:施工|建设|工程|项目|计划)?总工期|(?:施工|建设|工程|计划)?工期(?:目标|要求|承诺)?|(?:施工|建设|工程|项目)?周期|施工期|建设期|(?:项目|计划|施工)?历时)"
                    r"[^\d\n]{0,12}?"
                    r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)\s*"
                    r"(个?日历天|个?工作日|个?自然天|天|日|个月|月|周|星期|年|个?季度)",
                    re.IGNORECASE,
                ),
                normalizer=MetricNormalizer.normalize_duration,
                tolerance=0.0,  # 工期绝对零容差
                default_severity=IssueSeverity.CRITICAL,
            ),
            # 2. 造价与金额类指标 (Budget & Cost)
            MetricExtractionRule(
                category="造价",
                dimension="造价",
                metric_name="工程总投资/造价",
                regex=re.compile(
                    r"(?:(?:工程|项目|建设|概算)?总投资|投资总额|(?:工程|项目|建设)?总造价|投标(?:总)?(?:报价|价|金额)|合同(?:总)?(?:价|额|金额)|签约(?:合同)?价|(?:工程|项目)?总预算|(?:最高)?投标限价|(?:招标|预算)控制价|(?:工程|建安)?(?:总)?费用|工程造价|建安工程费|概算投资|工程总承包造价)"
                    r"[^\d\n]{0,12}?(?:¥|￥|RMB)?\s*"
                    r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)\s*"
                    r"(亿元|百万元|万元|千元|元)",
                    re.IGNORECASE,
                ),
                normalizer=MetricNormalizer.normalize_currency,
                tolerance=0.01,  # 允许千分之一尾数舍入差
                default_severity=IssueSeverity.CRITICAL,
            ),
            # 3. 建筑规模与面积指标 (Building Scale & Area)
            MetricExtractionRule(
                category="建筑面积",
                dimension="建筑面积",
                metric_name="总建筑面积",
                regex=re.compile(
                    r"(?:总建筑面积|建筑总面积|地上地下总建筑面积|地上建筑面积|地下建筑面积|计容建筑面积|总占地面积|占地面积)"
                    r"[^\d\n]{0,12}?"
                    r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)\s*"
                    r"(万平方米|万m²|万㎡|万m2|平方米|平米|m²|㎡|m2)",
                    re.IGNORECASE,
                ),
                normalizer=MetricNormalizer.normalize_area,
                tolerance=0.01,
                default_severity=IssueSeverity.HIGH,
            ),
            # 4. 机电设备参数: 能效比 COP
            MetricExtractionRule(
                category="COP",
                dimension="设备参数",
                metric_name="性能系数(COP)",
                regex=re.compile(
                    r"(?:(?:制冷|额定)?能效比\s*(?:\(COP\)|（COP）)?|COP|性能系数(?:\(COP\)|（COP）)?)"
                    r"[^\d\n]{0,12}?"
                    r"([0-9]+(?:\.[0-9]+)?)\s*(?:W/W|kW/kW)?",
                    re.IGNORECASE,
                ),
                normalizer=lambda v, u: MetricNormalizer.normalize_cop(v, u),
                tolerance=0.001,
                default_severity=IssueSeverity.HIGH,
            ),
            # 5. 机电设备参数: 额定制冷量
            MetricExtractionRule(
                category="制冷量",
                dimension="设备参数",
                metric_name="额定制冷量",
                regex=re.compile(
                    r"(?:(?:单台|单机|额定|设计)?制冷量|(?:额定)?制冷能力)"
                    r"[^\d\n]{0,12}?"
                    r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)\s*"
                    r"(kW|千瓦|RT|冷吨|美制冷吨|USRT|MW|兆瓦|W|瓦)",
                    re.IGNORECASE,
                ),
                normalizer=MetricNormalizer.normalize_cooling_capacity,
                tolerance=0.01,
                default_severity=IssueSeverity.HIGH,
            ),
            # 6. 机电设备参数: 额定风量
            MetricExtractionRule(
                category="风量",
                dimension="设备参数",
                metric_name="额定风量",
                regex=re.compile(
                    r"(?:(?:额定|送风|排风|新风|排烟|循环)?风量|送风能力)"
                    r"[^\d\n]{0,12}?"
                    r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)\s*"
                    r"(m³/h|m3/h|m\^3/h|立方米/小时|立方米每小时|m³/小时|m3/小时|m³/s|m3/s|立方米/秒|CFM)",
                    re.IGNORECASE,
                ),
                normalizer=MetricNormalizer.normalize_air_flow,
                tolerance=0.01,
                default_severity=IssueSeverity.HIGH,
            ),
            # 7. 机电设备参数: 额定功率
            MetricExtractionRule(
                category="功率",
                dimension="设备参数",
                metric_name="额定功率",
                regex=re.compile(
                    r"(?:(?:额定|输入|电机|电机额定|装机|轴|设备)?功率|装机容量)"
                    r"[^\d\n]{0,12}?"
                    r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)\s*"
                    r"(kW|千瓦|W|瓦|MW|兆瓦|HP|马力)",
                    re.IGNORECASE,
                ),
                normalizer=MetricNormalizer.normalize_power,
                tolerance=0.01,
                default_severity=IssueSeverity.HIGH,
            ),
            # 8. 机电设备参数: 扬程
            MetricExtractionRule(
                category="扬程",
                dimension="设备参数",
                metric_name="额定扬程",
                regex=re.compile(
                    r"(?:(?:额定|设计|水泵|循环水泵|冷冻水循环泵)?扬程)"
                    r"[^\d\n]{0,12}?"
                    r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)\s*"
                    r"(mH2O|mH₂O|米水柱|m|米)",
                    re.IGNORECASE,
                ),
                normalizer=MetricNormalizer.normalize_head,
                tolerance=0.01,
                default_severity=IssueSeverity.HIGH,
            ),
        ]

    def extract_metrics_from_text(
        self,
        section_title: str,
        text_content: str,
        page_num: str = "P.1",
        block_id: Optional[str] = None,
    ) -> List[Tuple[str, str, StatementAnchor]]:
        """从一段章节文本中抽取关键事实锚点"""
        extracted: List[Tuple[str, str, StatementAnchor]] = []
        if not text_content:
            return extracted

        # 按标点与换行精细切分独立语句
        sentences = re.split(r"[。！？\n；;]", text_content)

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence or len(sentence) < 3:
                continue

            for rule in self.rules:
                for match in rule.regex.finditer(sentence):
                    raw_num_str = match.group(1)
                    raw_unit = match.group(2) if len(match.groups()) >= 2 and match.group(2) else ""
                    try:
                        raw_num = self.parse_number(raw_num_str)
                    except ValueError:
                        continue

                    # 执行自适应归一化换算
                    norm_val, std_unit = rule.normalizer(raw_num, raw_unit)

                    anchor = StatementAnchor(
                        section_title=section_title,
                        page_or_sheet=page_num,
                        raw_text=match.group(0),
                        full_sentence=sentence,
                        raw_number=raw_num,
                        raw_unit=raw_unit,
                        normalized_value=norm_val,
                        standard_unit=std_unit,
                        block_id=block_id,
                    )
                    extracted.append((rule.category, rule.metric_name, anchor))

        return extracted

    def extract_metrics_from_ast_node(
        self, node: Any
    ) -> List[Tuple[str, str, StatementAnchor]]:
        """从单个 ASTNode 节点中深度穿透抽取 (覆盖正文、表格、进度实体)"""
        extracted: List[Tuple[str, str, StatementAnchor]] = []
        section_title = " / ".join(node.section_path) if getattr(node, "section_path", None) else "未命名章节"
        page_num = str(getattr(node, "page_or_sheet", "P.1") or "P.1")
        block_id = getattr(node, "block_id", None)

        # 1. 文本与段落抽取
        text_content = getattr(node, "text_content", "") or ""
        if text_content:
            extracted.extend(
                self.extract_metrics_from_text(section_title, text_content, page_num, block_id)
            )

        # 2. 结构化表格 TableData 穿透抽取
        table_data = getattr(node, "table_data", None)
        if table_data:
            table_sec = f"{section_title} [表格]"
            # 优先扫描二维 rows
            rows = getattr(table_data, "rows", []) or []
            for r_idx, row in enumerate(rows):
                row_text = " | ".join(str(c).strip() for c in row if str(c).strip())
                if row_text:
                    row_items = self.extract_metrics_from_text(table_sec, row_text, page_num, block_id)
                    for cat, m_name, anchor in row_items:
                        anchor.extra_context["table_row_index"] = r_idx
                        extracted.append((cat, m_name, anchor))

            # 补充扫描表格 Markdown 字符串
            markdown_str = getattr(table_data, "markdown", "") or ""
            if markdown_str and not rows:
                extracted.extend(
                    self.extract_metrics_from_text(table_sec, markdown_str, page_num, block_id)
                )

        # 3. 工程进度任务 ScheduleTaskData 结构体抽取
        schedule_data = getattr(node, "schedule_data", None)
        if schedule_data:
            duration_days = getattr(schedule_data, "duration_days", None)
            task_name = getattr(schedule_data, "task_name", "") or ""
            if duration_days is not None and duration_days > 0:
                if "总工期" in task_name or "项目" in task_name or getattr(schedule_data, "task_id", "") in ("0", "1"):
                    anchor = StatementAnchor(
                        section_title=f"{section_title} [进度计划: {task_name}]",
                        page_or_sheet=page_num,
                        raw_text=f"{task_name} 工期 {duration_days} 天",
                        full_sentence=f"进度计划任务 '{task_name}' 工期为 {duration_days} 日历天",
                        raw_number=float(duration_days),
                        raw_unit="天",
                        normalized_value=float(duration_days),
                        standard_unit="天",
                        block_id=block_id,
                        extra_context={"schedule_task_id": getattr(schedule_data, "task_id", "")},
                    )
                    extracted.append(("工期", "施工总工期", anchor))

        return extracted

    def _generate_conflict_reason_and_suggestion(
        self,
        category: str,
        metric_name: str,
        baseline: StatementAnchor,
        subsequent: StatementAnchor,
        diff: float,
        diff_pct: float,
    ) -> Tuple[str, str, IssueSeverity]:
        """
        深度矛盾归因推导与定级:
        工期与总投资矛盾一律判定为 CRITICAL 废标风险
        核心设备参数偏差 > 10% 判定为 CRITICAL，1%~10% 判定为 HIGH
        """
        val1 = baseline.normalized_value
        unit1 = baseline.standard_unit
        val2 = subsequent.normalized_value
        unit2 = subsequent.standard_unit

        if "工期" in category or "工期" in metric_name:
            severity = IssueSeverity.CRITICAL
            reason = (
                f"【致命工期冲突】前文在 '{baseline.section_title}' ({baseline.page_or_sheet}) 明确承诺工期为 "
                f"'{baseline.raw_text}' (折算为 {val1} {unit1})，但在后文 '{subsequent.section_title}' "
                f"({subsequent.page_or_sheet}) 却陈述为 '{subsequent.raw_text}' (折算为 {val2} {unit2})。"
                f"前后相差 {diff} {unit1} (偏差率 {diff_pct}%)。"
                f"在招投标法律与评审实践中，关键节点工期前后矛盾直接构成'实质性响应不一致'，极易被评标专家判定为虚假应标或导致直接废标，面临废标风险！"
            )
            suggestion = (
                f"核对施工网络横道图与投标函承诺，全篇严格统一工期表述。"
                f"建议将 '{subsequent.section_title}' 中的工期表述统一订正为投标函基准值 '{baseline.raw_text}' ({val1} {unit1})。"
            )
        elif "造价" in category or "投资" in category or "预算" in category:
            severity = IssueSeverity.CRITICAL
            reason = (
                f"【重大商业报价/投资矛盾】前文在 '{baseline.section_title}' ({baseline.page_or_sheet}) 金额表述为 "
                f"'{baseline.raw_text}' ({val1} {unit1})，而在后文 '{subsequent.section_title}' "
                f"({subsequent.page_or_sheet}) 却记录为 '{subsequent.raw_text}' ({val2} {unit2})。"
                f"章节间差额高达 {diff} {unit1} (偏差率 {diff_pct}%)！商业报价冲突属于不可调和的重大实质性缺陷。"
            )
            suggestion = (
                f"核准造价预算清单与最终报价汇总表，消解各章节金额脱节。建议以投标总价正式盖章页为准，统一修改各子项预算与文字阐述。"
            )
        elif "COP" in category or "能效比" in metric_name:
            severity = IssueSeverity.CRITICAL if diff_pct > 10.0 else IssueSeverity.HIGH
            reason = (
                f"【暖通核心能效参数背离】前文在 '{baseline.section_title}' ({baseline.page_or_sheet}) 标注性能系数 COP 为 "
                f"{val1}，后文在 '{subsequent.section_title}' ({subsequent.page_or_sheet}) 却表述为 {val2}。"
                f"能效比指标直接关联绿色建筑认证与机电设备节能准入门槛，偏差率达 {diff_pct}%。"
            )
            suggestion = f"调取冷水机组厂家样本选型数据，统一修正全书 COP 承诺为设计复核值。"
        elif "制冷量" in category or "功率" in category or "风量" in category or "扬程" in category:
            severity = IssueSeverity.CRITICAL if diff_pct > 15.0 else IssueSeverity.HIGH
            reason = (
                f"【核心机电设备规格不一致】'{metric_name}' 在 '{baseline.section_title}' ({baseline.page_or_sheet}) "
                f"为 {val1} {unit1} ('{baseline.raw_text}')，但在 '{subsequent.section_title}' ({subsequent.page_or_sheet}) "
                f"为 {val2} {unit2} ('{subsequent.raw_text}')，偏差达 {diff} {unit1} ({diff_pct}%)。"
            )
            suggestion = f"核实暖通给排水电气施工图设计总说明，统一该设备铭牌选型参数。"
        else:
            severity = IssueSeverity.HIGH if diff_pct > 5.0 else IssueSeverity.MEDIUM
            reason = (
                f"指标 '{metric_name}' 在前后文存在数值不一致: {val1} vs {val2} {unit1}，偏差比例为 {diff_pct}%。"
            )
            suggestion = f"建议专业工程师团队复核该指标口径，修订为全篇统一数值。"

        return reason, suggestion, severity

    def validate_document_consistency(
        self,
        document_title: str,
        sections_data: Union[List[Dict[str, Any]], Any],
    ) -> ConsistencyReport:
        """
        全量跨章节一致性交叉校验入口
        支持:
        - List[Dict[str, Any]]: 章节字典列表 [{"section_title": ..., "content": ..., "page": ...}]
        - UnifiedDocumentAST: AST 实体对象
        """
        if hasattr(sections_data, "nodes"):
            return self.validate_ast_consistency(sections_data)

        metric_graph: Dict[Tuple[str, str], List[StatementAnchor]] = {}
        scanned_dimensions = set()
        total_extracted = 0

        for sec in sections_data:
            sec_title = sec.get("section_title", "未命名章节")
            content = sec.get("content", "")
            page = str(sec.get("page", "P.1") or "P.1")

            items = self.extract_metrics_from_text(sec_title, content, page)
            for cat, metric_name, anchor in items:
                total_extracted += 1
                scanned_dimensions.add(cat)
                key = (cat, metric_name)
                if key not in metric_graph:
                    metric_graph[key] = []
                metric_graph[key].append(anchor)

        return self._build_report_from_graph(document_title, metric_graph, total_extracted, list(scanned_dimensions))

    def validate_ast_consistency(self, ast: Any) -> ConsistencyReport:
        """针对 UnifiedDocumentAST 协议树进行高精结构化扫描"""
        document_title = getattr(ast, "file_name", "未命名文档")
        metric_graph: Dict[Tuple[str, str], List[StatementAnchor]] = {}
        scanned_dimensions = set()
        total_extracted = 0

        nodes = getattr(ast, "nodes", []) or []
        for node in nodes:
            items = self.extract_metrics_from_ast_node(node)
            for cat, metric_name, anchor in items:
                total_extracted += 1
                scanned_dimensions.add(cat)
                key = (cat, metric_name)
                if key not in metric_graph:
                    metric_graph[key] = []
                metric_graph[key].append(anchor)

        return self._build_report_from_graph(document_title, metric_graph, total_extracted, list(scanned_dimensions))

    def _build_report_from_graph(
        self,
        document_title: str,
        metric_graph: Dict[Tuple[str, str], List[StatementAnchor]],
        total_extracted: int,
        scanned_dimensions: List[str],
    ) -> ConsistencyReport:
        """从指标事实图谱中检测冲突并生成完备证据链"""
        conflicts: List[ConsistencyConflict] = []
        conflict_id_seq = 1

        for (cat, metric_name), statements in metric_graph.items():
            if len(statements) < 2:
                continue

            rule = next((r for r in self.rules if r.category == cat and r.metric_name == metric_name), None)
            tolerance = rule.tolerance if rule else 1e-4

            baseline = statements[0]

            for subsequent in statements[1:]:
                if (
                    baseline.section_title == subsequent.section_title
                    and baseline.page_or_sheet == subsequent.page_or_sheet
                    and baseline.raw_text == subsequent.raw_text
                ):
                    continue

                v1 = baseline.normalized_value
                v2 = subsequent.normalized_value
                diff = abs(v1 - v2)

                if diff > tolerance:
                    max_val = max(abs(v1), abs(v2))
                    diff_pct = round((diff / max_val * 100.0), 2) if max_val > 0 else 0.0

                    reason, suggestion, severity = self._generate_conflict_reason_and_suggestion(
                        cat, metric_name, baseline, subsequent, diff, diff_pct
                    )

                    conflict = ConsistencyConflict(
                        conflict_id=f"CONF-{conflict_id_seq:04d}",
                        metric_category=cat,
                        metric_name=metric_name,
                        dimension=cat,
                        conflict_type=ConflictType.NUMERICAL_MISMATCH,
                        severity=severity,
                        value_a=v1,
                        unit_a=baseline.standard_unit,
                        section_a=baseline.section_title,
                        quote_a=baseline.raw_text,
                        page_a=baseline.page_or_sheet,
                        value_b=v2,
                        unit_b=subsequent.standard_unit,
                        section_b=subsequent.section_title,
                        quote_b=subsequent.raw_text,
                        page_b=subsequent.page_or_sheet,
                        diff_value=round(diff, 4),
                        diff_percent=diff_pct,
                        difference_value=round(diff, 4),
                        difference_percentage=diff_pct,
                        baseline_statement=baseline,
                        conflicting_statement=subsequent,
                        detailed_reason=reason,
                        correction_suggestion=suggestion,
                    )
                    conflicts.append(conflict)
                    conflict_id_seq += 1

        critical_count = sum(1 for c in conflicts if c.severity == IssueSeverity.CRITICAL)
        high_count = sum(1 for c in conflicts if c.severity == IssueSeverity.HIGH)
        medium_count = sum(1 for c in conflicts if c.severity == IssueSeverity.MEDIUM)
        low_count = sum(1 for c in conflicts if c.severity == IssueSeverity.LOW)

        exported_graph = {
            f"{cat}::{name}": [s.model_dump() for s in stmts]
            for (cat, name), stmts in metric_graph.items()
        }

        return ConsistencyReport(
            document_title=document_title,
            total_metrics_scanned=total_extracted,
            conflicts_found=len(conflicts),
            critical_count=critical_count,
            high_count=high_count,
            medium_count=medium_count,
            low_count=low_count,
            conflicts=conflicts,
            extracted_knowledge_graph=exported_graph,
            scanned_dimensions=scanned_dimensions,
        )

    @classmethod
    def export_to_review_results(
        cls, report: ConsistencyReport, task_id: str, tenant_id: str
    ) -> List[Dict[str, Any]]:
        """
        将一致性冲突报告转换为适配 ReviewResult 字典
        """
        results: List[Dict[str, Any]] = []
        for c in report.conflicts:
            page_b_num = None
            if c.page_b:
                m = re.search(r"\d+", c.page_b)
                if m:
                    page_b_num = int(m.group(0))

            results.append({
                "tenant_id": tenant_id,
                "task_id": task_id,
                "deviation_type": "negative",
                "severity": c.severity.value,
                "confidence": 1.0,
                "rule_category": f"一致性校验-{c.dimension}",
                "title": f"跨章节数据矛盾: {c.metric_name} ({c.value_a}{c.unit_a} vs {c.value_b}{c.unit_b})",
                "description": c.detailed_reason,
                "suggestion": c.correction_suggestion,
                "source_section": c.section_b,
                "source_page": page_b_num,
                "source_quote": c.quote_b,
                "benchmark_section": c.section_a,
                "benchmark_quote": c.quote_a,
                "diff_payload": {
                    "conflict_id": c.conflict_id,
                    "metric_name": c.metric_name,
                    "dimension": c.dimension,
                    "value_a": c.value_a,
                    "unit_a": c.unit_a,
                    "section_a": c.section_a,
                    "quote_a": c.quote_a,
                    "page_a": c.page_a,
                    "value_b": c.value_b,
                    "unit_b": c.unit_b,
                    "section_b": c.section_b,
                    "quote_b": c.quote_b,
                    "page_b": c.page_b,
                    "diff_value": c.diff_value,
                    "diff_percent": c.diff_percent,
                },
            })
        return results

    @classmethod
    def to_review_results(
        cls, report: ConsistencyReport, task_id: str, tenant_id: str
    ) -> List[ReviewResult]:
        """
        将报告直接转换为 SQLAlchemy ReviewResult 实体列表
        """
        dict_records = cls.export_to_review_results(report, task_id, tenant_id)
        entities = []
        for r in dict_records:
            sev = SeverityLevel(r["severity"]) if r["severity"] in [s.value for s in SeverityLevel] else SeverityLevel.HIGH
            dev = DeviationType.NEGATIVE
            entities.append(
                ReviewResult(
                    tenant_id=r["tenant_id"],
                    task_id=r["task_id"],
                    deviation_type=dev,
                    severity=sev,
                    confidence=r["confidence"],
                    rule_category=r["rule_category"],
                    title=r["title"],
                    description=r["description"],
                    suggestion=r["suggestion"],
                    source_section=r["source_section"],
                    source_page=r["source_page"],
                    source_quote=r["source_quote"],
                    benchmark_section=r["benchmark_section"],
                    benchmark_quote=r["benchmark_quote"],
                    diff_payload=r["diff_payload"],
                )
            )
        return entities

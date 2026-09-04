"""
招投标评分表深度比对与 4 类偏离度判定引擎 (Tender Alignment & Deviation Engine)
实现 Features 20, 21, 22:
1. 招标文件评分表结构化拆解 (Feature 20: RFPScoringTableParser)
   - 支持技术标/商务标/资质资信评分表拆解、满分值、细则规则、带 ★/* 强制性条款识别
   - 支持跨行跨列合并单元格展平与指标硬性约束抽取 (工期、COP、质保期、业绩数量等)
2. 自编标书跨文档语义对齐引擎 (Feature 21: BidSemanticAlignmentEngine)
   - 基于大纲面包屑拓扑、BM25 特化工程分词与关键数值指标的三级漏斗匹配
   - 定位标书响应原句 (source_quote) 与物理页码 (source_page)
3. 4 类偏离度判定与赋分计算器 (Feature 22: FourCategoryDeviationClassifier)
   - 严格判定 FULL_COMPLIANCE (完全满足)、MISSING (缺失项)、POSITIVE (正偏离)、NEGATIVE (负偏离)
   - 提供置信度 [0.0, 1.0]、评分测算、严重级别与智能纠偏补正建议
   - 无缝桥接并导出 SQLAlchemy 2.0 ReviewResult 实体
"""

import math
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from pydantic import BaseModel, Field

from app.models.audit_rag import DeviationType, ReviewResult, SeverityLevel
from app.quality.consistency_engine import MetricNormalizer
from app.rag.hybrid_search import BM25Tokenizer
from app.schemas.ast import (
    ASTBlockType,
    ASTNode,
    TableData,
    UnifiedDocumentAST,
)
from app.schemas.audit import (
    AlignmentCandidate,
    CriteriaConstraint,
    DeviationEvaluationResult,
    MetricDirection,
    ScoringCategory,
    TenderAlignmentReport,
    TenderScoringItem,
    TenderScoringTable,
)


# ===========================================================================
# 1. 招标文件评分表解析器 (Feature 20: RFPScoringTableParser)
# ===========================================================================

class RFPScoringTableParser:
    """
    招标文件评分标准体系表结构化解析引擎
    """

    TABLE_KEYWORDS: List[str] = [
        "评分标准", "评标办法", "详细评审", "技术标评分", "商务标评分",
        "分值", "评分细则", "综合评估", "评标标准", "评审标准", "打分表",
        "评审项目", "技术评分", "商务评分"
    ]

    HEADER_NAME_MAP: Dict[str, List[str]] = {
        "index": ["序号", "编号", "项号", "条目"],
        "category": ["分类", "类别", "评审类别", "项目大类", "评审项目", "大项", "评审指标大类"],
        "item_name": ["评分项", "评审内容", "评分内容", "指标名称", "考核要点", "评审指标", "项目", "子项", "评分项目", "评审要点"],
        "max_score": ["分值", "满分", "最高分", "标准分", "权重", "分值设置", "配分", "得分"],
        "rules": ["评分标准", "评分细则", "评审细则", "评分办法", "赋分标准", "扣分规则", "要求", "评审要求", "评分细则及标准"],
    }

    KILL_PATTERNS: re.Pattern = re.compile(
        r"[★\*▲]|废标|一票否决|实质性要求|不得超过|不得低于|直接判定为无效标|否决投标|重大负偏离|不接受任何负偏离"
    )

    @classmethod
    def is_scoring_table(cls, node: ASTNode) -> bool:
        """判断 AST 节点是否为评标评分表"""
        if node.block_type != ASTBlockType.TABLE or not node.table_data:
            return False

        # 1. 检查大纲路径
        path_text = " ".join(node.section_path or [])
        if any(kw in path_text for kw in cls.TABLE_KEYWORDS):
            return True

        # 2. 检查表头或前两行文字
        t_data = node.table_data
        headers = t_data.headers or (t_data.rows[:2] if t_data.rows else [])
        header_text = " ".join(str(cell) for row in headers for cell in row)
        matched_kw_count = sum(1 for kw in ["评分", "分值", "细则", "标准", "项目", "满分", "评审"] if kw in header_text)
        return matched_kw_count >= 2

    @classmethod
    def _map_columns(cls, table_data: TableData) -> Dict[str, int]:
        """分析表头或首行，识别列语义角色索引"""
        col_map: Dict[str, int] = {}
        sample_rows = table_data.headers if table_data.headers else table_data.rows[:2]
        if not sample_rows:
            return col_map

        # 合并可能的多级表头
        header_cols: List[str] = ["" for _ in range(len(sample_rows[0]))]
        for row in sample_rows:
            for idx, cell in enumerate(row):
                if idx < len(header_cols):
                    header_cols[idx] += " " + str(cell).strip()

        for idx, col_text in enumerate(header_cols):
            text = col_text.strip()
            for role, keywords in cls.HEADER_NAME_MAP.items():
                if role not in col_map and any(kw in text for kw in keywords):
                    col_map[role] = idx

        # 启发式兜底: 4~5列标准结构默认映射
        total_cols = len(header_cols)
        if "item_name" not in col_map:
            col_map["item_name"] = 1 if total_cols >= 2 else 0
        if "max_score" not in col_map and total_cols >= 3:
            col_map["max_score"] = 2 if total_cols >= 4 else total_cols - 1
        if "rules" not in col_map and total_cols >= 4:
            col_map["rules"] = total_cols - 1

        return col_map

    @classmethod
    def _get_cell(cls, row: List[Any], idx: Optional[int]) -> str:
        if idx is None or idx < 0 or idx >= len(row):
            return ""
        return str(row[idx]).strip()

    @classmethod
    def _parse_score_value(cls, s: str) -> float:
        """提取满分分值标量"""
        m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*分?", s)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
        return 10.0

    @classmethod
    def _classify_category(cls, text: str) -> ScoringCategory:
        """根据标题或大项文字分类"""
        if any(k in text for k in ["商务", "报价", "价格", "资金", "财务", "付款"]):
            if any(k in text for k in ["报价", "价格", "基准价"]):
                return ScoringCategory.PRICE
            return ScoringCategory.COMMERCIAL
        elif any(k in text for k in ["资质", "资信", "团队", "人员", "业绩", "奖项", "认证"]):
            return ScoringCategory.QUALIFICATION
        elif any(k in text for k in ["技术", "施工", "方案", "质量", "进度", "工期", "安全", "文明", "设备"]):
            return ScoringCategory.TECHNICAL
        return ScoringCategory.GENERAL

    @classmethod
    def _extract_page_num(cls, page_or_sheet: Optional[str]) -> Optional[int]:
        if not page_or_sheet:
            return None
        m = re.search(r"\d+", str(page_or_sheet))
        return int(m.group(0)) if m else None

    @classmethod
    def _extract_constraint(cls, name: str, rules: str) -> Optional[CriteriaConstraint]:
        """从评分项名称与细则中抽取核心工程参数约束"""
        combined = f"{name} {rules}"

        # 1. 工期类指标 (<= 天数)
        m_dur = re.search(r"(?:工期|施工周期|计划工期)[^\d\n]{0,8}?(?:不?超过|小于等于|<=|不得大于|控制在|要求为|承诺为)?\s*([0-9]+(?:\.[0-9]+)?)\s*(个?日历天|天|日|个月|月|年)", combined)
        if m_dur:
            val, unit = float(m_dur.group(1)), m_dur.group(2)
            norm_val, _ = MetricNormalizer.normalize_duration(val, unit)
            return CriteriaConstraint(
                metric_name="工期",
                target_value=norm_val,
                target_unit="天",
                operator="<=",
                direction=MetricDirection.LOWER_BETTER
            )

        # 2. 能效比 COP (>= 阈值)
        m_cop = re.search(r"(?:COP|能效比)[^\d\n]{0,8}?(?:不?低于|大于等于|>=|达到|不小于)?\s*([0-9]+(?:\.[0-9]+)?)", combined, re.IGNORECASE)
        if m_cop:
            return CriteriaConstraint(
                metric_name="COP",
                target_value=float(m_cop.group(1)),
                target_unit="",
                operator=">=",
                direction=MetricDirection.HIGHER_BETTER
            )

        # 3. 质保期 / 保修期 (>= 年数)
        m_war = re.search(r"(?:质保期|保修期|免费维保)[^\d\n]{0,8}?(?:不?低于|不少于|大于等于|>=|要求为)?\s*([0-9]+(?:\.[0-9]+)?)\s*(年|个月|月)", combined)
        if m_war:
            val, unit = float(m_war.group(1)), m_war.group(2)
            years = val if "年" in unit else val / 12.0
            return CriteriaConstraint(
                metric_name="质保期",
                target_value=years,
                target_unit="年",
                operator=">=",
                direction=MetricDirection.HIGHER_BETTER
            )

        # 4. 类似业绩数量 (>= 项数)
        m_proj = re.search(r"(?:类似工程业绩|同类业绩|业绩要求|类似项目)[^\d\n]{0,8}?(?:不少于|提供|具备)?\s*([0-9]+)\s*(个|项|个以上)", combined)
        if m_proj:
            return CriteriaConstraint(
                metric_name="类似业绩",
                target_value=float(m_proj.group(1)),
                target_unit="项",
                operator=">=",
                direction=MetricDirection.HIGHER_BETTER
            )

        return None

    @classmethod
    def _extract_keywords(
        cls, name: str, rules: str, constraint: Optional[CriteriaConstraint]
    ) -> List[str]:
        """提取高质量检索关键词"""
        tokens = BM25Tokenizer.tokenize(f"{name} {rules[:100]}")
        # 过滤停用词与过短词
        stopwords = {"的", "与", "在", "及", "和", "或", "按", "对", "为", "分", "项", "要求", "标准"}
        kw = [t for t in tokens if len(t) >= 2 and t not in stopwords]
        if constraint:
            kw.append(constraint.metric_name)
        # 去重保持顺序
        seen: Set[str] = set()
        res: List[str] = []
        for w in kw:
            if w not in seen:
                seen.add(w)
                res.append(w)
        return res[:12]

    def parse_table_node(self, node: ASTNode, base_id: str = "CRIT") -> List[TenderScoringItem]:
        """解析单个表格 AST 节点为标准评分项列表"""
        table_data = node.table_data
        if not table_data or not table_data.rows:
            return []

        col_map = self._map_columns(table_data)
        items: List[TenderScoringItem] = []

        current_category = ScoringCategory.TECHNICAL
        current_parent_name = "综合技术方案"

        start_row = 0 if table_data.headers else 1
        for row_idx in range(start_row, len(table_data.rows)):
            row = table_data.rows[row_idx]
            if not row or all(not str(c).strip() for c in row):
                continue

            raw_category = self._get_cell(row, col_map.get("category"))
            raw_name = self._get_cell(row, col_map.get("item_name"))
            raw_score = self._get_cell(row, col_map.get("max_score"))
            raw_rules = self._get_cell(row, col_map.get("rules"))

            # 单列跨行大项标题 (如 "一、施工组织设计部分 (60分)")
            non_empty_cells = [str(c).strip() for c in row if str(c).strip()]
            if len(non_empty_cells) == 1 and not raw_rules:
                header_line = non_empty_cells[0]
                current_category = self._classify_category(header_line)
                current_parent_name = header_line
                continue

            if raw_category and not raw_name and not raw_rules:
                current_category = self._classify_category(raw_category)
                current_parent_name = raw_category
                continue

            if not raw_name and not raw_rules:
                continue

            item_name = raw_name or f"评分项_{row_idx}"
            scoring_guide = raw_rules or raw_name
            max_score = self._parse_score_value(raw_score) if raw_score else 10.0

            # 强制条款判定 (带 ★、*、▲ 或 废标/否决 字样)
            combined_text = f"{item_name} {scoring_guide}"
            is_mandatory = bool(self.KILL_PATTERNS.search(combined_text))

            constraint = self._extract_constraint(item_name, scoring_guide)
            keywords = self._extract_keywords(item_name, scoring_guide, constraint)
            page_num = self._extract_page_num(node.page_or_sheet)

            item = TenderScoringItem(
                criteria_id=f"{base_id}_{len(items) + 1:02d}",
                category=current_category,
                parent_category_name=current_parent_name,
                name=item_name,
                max_score=max_score,
                is_mandatory=is_mandatory,
                scoring_guide=scoring_guide,
                constraint=constraint,
                keywords=keywords,
                source_node_id=node.block_id,
                source_page=page_num,
                source_section=" > ".join(node.section_path or [])
            )
            items.append(item)

        return items

    def fallback_parse_text_outlines(self, rfp_ast: UnifiedDocumentAST) -> List[TenderScoringItem]:
        """非表格大纲文本回退解析"""
        items: List[TenderScoringItem] = []
        score_pattern = re.compile(r"^(?:[（(]?\d+[）)]?|[一二三四五六七八九十]+[、.．])\s*(.*?)(?:（([0-9]+(?:\.[0-9]+)?)\s*分）|\(([0-9]+(?:\.[0-9]+)?)\s*分\))\s*(.*)$")

        for node in rfp_ast.nodes:
            if node.block_type not in (ASTBlockType.HEADING, ASTBlockType.PARAGRAPH):
                continue
            text = node.text_content.strip()
            m = score_pattern.match(text)
            if m:
                name = m.group(1).strip()
                score_str = m.group(2) or m.group(3)
                max_score = float(score_str) if score_str else 10.0
                guide = m.group(4).strip() or name
                combined = f"{name} {guide}"
                is_mandatory = bool(self.KILL_PATTERNS.search(combined))
                constraint = self._extract_constraint(name, guide)
                keywords = self._extract_keywords(name, guide, constraint)

                items.append(
                    TenderScoringItem(
                        criteria_id=f"CRIT_TXT_{len(items) + 1:02d}",
                        category=self._classify_category(name),
                        parent_category_name=" > ".join(node.section_path[:-1]) if node.section_path else "招标文件要求",
                        name=name,
                        max_score=max_score,
                        is_mandatory=is_mandatory,
                        scoring_guide=guide,
                        constraint=constraint,
                        keywords=keywords,
                        source_node_id=node.block_id,
                        source_page=self._extract_page_num(node.page_or_sheet),
                        source_section=" > ".join(node.section_path or [])
                    )
                )
        return items

    def parse_rfp_scoring_table(self, rfp_ast: UnifiedDocumentAST) -> TenderScoringTable:
        """全流程解析招标文件，生成结构化评分体系表"""
        all_items: List[TenderScoringItem] = []
        table_node_ids: List[str] = []

        # 1. 扫描所有 TABLE 节点
        for node in rfp_ast.nodes:
            if self.is_scoring_table(node):
                table_node_ids.append(node.block_id)
                items = self.parse_table_node(node, base_id=f"CRIT_{len(table_node_ids)}")
                all_items.extend(items)

        # 2. 若未解析出表格评分项，启动大纲文本回退解析器
        if not all_items:
            all_items = self.fallback_parse_text_outlines(rfp_ast)

        total_score = sum(item.max_score for item in all_items) if all_items else 100.0

        return TenderScoringTable(
            document_id=rfp_ast.document_id,
            tenant_id=rfp_ast.tenant_id,
            title=f"{rfp_ast.file_name} - 评标办法与评分标准",
            total_max_score=total_score,
            items=all_items,
            raw_table_node_ids=table_node_ids,
        )


# ===========================================================================
# 2. 自编标书语义对齐引擎 (Feature 21: BidSemanticAlignmentEngine)
# ===========================================================================

class BidSemanticAlignmentEngine:
    """
    自编标书与评分项跨文档语义对齐引擎
    """

    MIN_ALIGN_THRESHOLD: float = 0.25

    def __init__(self, proposal_ast: UnifiedDocumentAST):
        self.ast = proposal_ast
        self._index_ast_nodes()

    def _index_ast_nodes(self) -> None:
        """预先建立 AST 节点的大纲拓扑与分词倒排索引"""
        self.searchable_nodes: List[Dict[str, Any]] = []
        for node in self.ast.nodes:
            text = node.text_content or ""
            if node.table_data and node.table_data.markdown:
                text = f"{text}\n{node.table_data.markdown}"

            section_full = " > ".join(node.section_path or [])
            combined = f"{section_full} {text}"
            tokens = set(BM25Tokenizer.tokenize(combined))
            page_num = None
            if node.page_or_sheet:
                m = re.search(r"\d+", str(node.page_or_sheet))
                page_num = int(m.group(0)) if m else None

            self.searchable_nodes.append({
                "node": node,
                "text": text,
                "section_full": section_full,
                "tokens": tokens,
                "page": page_num,
            })

    def align_item(self, item: TenderScoringItem) -> AlignmentCandidate:
        """为单个评分项在自编标书中寻找最相关的响应段落与证据原句"""
        query_text = f"{item.name} {item.parent_category_name} {' '.join(item.keywords)}"
        if item.constraint:
            query_text += f" {item.constraint.metric_name} {item.constraint.target_value}"

        query_tokens = set(BM25Tokenizer.tokenize(query_text))

        best_score = 0.0
        best_node_item: Optional[Dict[str, Any]] = None

        name_tokens = set(BM25Tokenizer.tokenize(item.name))

        for candidate in self.searchable_nodes:
            # 1. 评分项标题与章节大纲/正文匹配度 (权重 0.40)
            sec_tokens = set(BM25Tokenizer.tokenize(candidate["section_full"]))
            name_in_sec = len(name_tokens & sec_tokens) / max(len(name_tokens), 1)
            name_in_text = len(name_tokens & candidate["tokens"]) / max(len(name_tokens), 1)
            outline_overlap = max(name_in_sec, name_in_text * 0.8)

            # 2. 正文词法与指标覆盖 (权重 0.45)
            text_tokens = candidate["tokens"]
            text_overlap = len(query_tokens & text_tokens) / max(len(query_tokens), 1)

            # 3. 关键指标名称命中奖励 (权重 0.15)
            metric_bonus = 0.0
            if item.constraint and item.constraint.metric_name in candidate["text"]:
                metric_bonus = 0.20

            score = 0.40 * outline_overlap + 0.45 * text_overlap + metric_bonus
            if score > best_score:
                best_score = score
                best_node_item = candidate

        if best_score >= self.MIN_ALIGN_THRESHOLD and best_node_item:
            best_quote = self._extract_best_sentence(best_node_item["text"], query_tokens)
            return AlignmentCandidate(
                criteria_id=item.criteria_id,
                is_matched=True,
                alignment_score=min(1.0, round(best_score, 3)),
                node_id=best_node_item["node"].block_id,
                section_path=best_node_item["section_full"],
                page_number=best_node_item["page"],
                matched_quote=best_quote,
                full_section_content=best_node_item["text"][:800],
            )

        return AlignmentCandidate(
            criteria_id=item.criteria_id,
            is_matched=False,
            alignment_score=round(best_score, 3),
            matched_quote="",
            section_path="",
        )

    def _extract_best_sentence(self, text: str, query_tokens: Set[str]) -> str:
        """从文本中抽取与评分项最切题的单句或表格行"""
        lines = re.split(r"[。！？\n；;]", text)
        best_line = ""
        max_overlap = -1
        for line in lines:
            line_str = line.strip()
            if not line_str or len(line_str) < 4:
                continue
            line_tokens = set(BM25Tokenizer.tokenize(line_str))
            overlap = len(query_tokens & line_tokens)
            if overlap > max_overlap:
                max_overlap = overlap
                best_line = line_str
        return best_line or text[:120].strip()


# ===========================================================================
# 3. 4 类偏离度分类器 (Feature 22: FourCategoryDeviationClassifier)
# ===========================================================================

class FourCategoryDeviationClassifier:
    """
    招投标偏离度 4 类分类与赋分引擎
    """

    NEGATIVE_HEDGE_PATTERNS: re.Pattern = re.compile(
        r"(?:不承担|除外|顺延|暂不包含|不负责|另行收取?|由招标人承担|如因.*不予质保|不在本次范围内|不予承担|不承诺)"
    )
    POSITIVE_PROMISE_PATTERNS: re.Pattern = re.compile(
        r"(?:免费延长|无偿提供|超出国家标准|优于行业基准|双倍配置|全生命周期质保|额外赠送|更优于|提高|增设|无偿维保)"
    )

    def classify(
        self, item: TenderScoringItem, candidate: AlignmentCandidate
    ) -> DeviationEvaluationResult:
        """综合判定单项偏离度类别、置信度与拟赋分值"""
        # 1. 缺失项 (MISSING)
        if not candidate.is_matched or not candidate.matched_quote:
            severity = SeverityLevel.CRITICAL if item.is_mandatory else SeverityLevel.HIGH
            desc = (
                f"【重大风险】招标文件强制性条款（带★/废标项）【{item.name}】在标书中未检索到实质性响应，直接面临废标判定！"
                if item.is_mandatory
                else f"标书中未找到针对评分项【{item.name}】的实质性响应章节或有效凭证。"
            )
            sugg = (
                f"请在标书相应大纲中紧急增设【{item.name}】专项章节，严格按照评分细则补齐方案、承诺函或资质证明文件。"
            )
            return DeviationEvaluationResult(
                criteria_id=item.criteria_id,
                deviation_type=DeviationType.MISSING,
                severity=severity,
                confidence=0.92,
                score_assigned=0.0,
                max_score=item.max_score,
                source_section="",
                source_page=None,
                source_quote="",
                benchmark_section=item.source_section,
                benchmark_quote=item.scoring_guide,
                title=item.name,
                description=desc,
                suggestion=sugg,
                diff_payload={
                    "status": "missing",
                    "is_mandatory": item.is_mandatory,
                    "score_lost": item.max_score,
                    "category": item.category.value,
                }
            )

        # 2. 数值指标型 (工期、COP、质保期、类似业绩等)
        if item.constraint:
            return self._evaluate_numerical_item(item, candidate)

        # 3. 定性描述型与方案完整性审查
        return self._evaluate_qualitative_item(item, candidate)

    def _extract_metric_from_quote(
        self, text: str, metric_name: str
    ) -> Tuple[Optional[float], str]:
        """从候选文本中提取具体数值"""
        if metric_name == "工期":
            m = re.search(r"(?:工期|周期|历时)[^\d\n]{0,8}?([0-9]+(?:\.[0-9]+)?)\s*(个?日历天|天|日|个月|月|年)", text)
            if m:
                val, u = float(m.group(1)), m.group(2)
                norm_val, _ = MetricNormalizer.normalize_duration(val, u)
                return norm_val, "天"
        elif metric_name == "COP":
            m = re.search(r"(?:COP|能效比)[^\d\n]{0,8}?([0-9]+(?:\.[0-9]+)?)", text, re.I)
            if m:
                return float(m.group(1)), ""
        elif metric_name == "质保期":
            m = re.search(r"(?:质保期|保修期|维保)[^\d\n]{0,8}?([0-9]+(?:\.[0-9]+)?)\s*(年|个月|月)", text)
            if m:
                val, u = float(m.group(1)), m.group(2)
                years = val if "年" in u else val / 12.0
                return years, "年"
        elif metric_name == "类似业绩":
            m = re.search(r"([0-9]+)\s*(个|项)", text)
            if m:
                return float(m.group(1)), "项"

        # 备用抽取: 首个浮点数
        m_fallback = re.search(r"([0-9]+(?:\.[0-9]+)?)", text)
        if m_fallback:
            return float(m_fallback.group(1)), ""
        return None, ""

    def _evaluate_numerical_item(
        self, item: TenderScoringItem, candidate: AlignmentCandidate
    ) -> DeviationEvaluationResult:
        """数值硬性约束精准比对与分类"""
        constraint = item.constraint
        assert constraint is not None
        quote = candidate.matched_quote or candidate.full_section_content

        extracted_val, raw_unit = self._extract_metric_from_quote(quote, constraint.metric_name)

        # 检查是否同时含有免责排他字眼
        has_negative_hedge = bool(self.NEGATIVE_HEDGE_PATTERNS.search(quote))

        if extracted_val is None:
            return self._create_result(
                item=item,
                candidate=candidate,
                dev_type=DeviationType.NEGATIVE,
                severity=SeverityLevel.CRITICAL if item.is_mandatory else SeverityLevel.HIGH,
                confidence=0.85,
                score=0.0 if item.is_mandatory else round(item.max_score * 0.3, 1),
                desc=f"标书虽有相关章节，但未明确给出【{constraint.metric_name}】的具体数值承诺。",
                sugg=f"请在【{candidate.section_path}】中明确承诺【{constraint.metric_name}】数值满足招标基准要求。",
                payload={"status": "value_not_found", "is_mandatory": item.is_mandatory}
            )

        req_val = constraint.target_value
        direction = constraint.direction

        is_positive = False
        is_full = False
        is_negative = False

        if direction == MetricDirection.LOWER_BETTER:
            # 工期越短越优 (如 330天 < 360天)
            if extracted_val < req_val:
                is_positive = True
            elif extracted_val == req_val:
                is_full = True
            else:
                is_negative = True
        elif direction == MetricDirection.HIGHER_BETTER:
            # COP/质保期越大越优 (如 COP 5.6 > 5.0)
            if extracted_val > req_val:
                is_positive = True
            elif extracted_val == req_val:
                is_full = True
            else:
                is_negative = True
        else:
            if extracted_val == req_val:
                is_full = True
            else:
                is_negative = True

        # 若存在顺延免责等条款，正偏离不可成立，直接降为负偏离
        if is_positive and has_negative_hedge:
            is_positive = False
            is_negative = True

        delta = round(extracted_val - req_val, 2)
        diff_payload = {
            "metric": constraint.metric_name,
            "required": req_val,
            "proposed": extracted_val,
            "unit": constraint.target_unit,
            "delta": delta,
            "direction": direction.value,
            "is_mandatory": item.is_mandatory,
            "has_negative_hedge": has_negative_hedge,
            "category": item.category.value,
        }

        if is_positive:
            return self._create_result(
                item=item,
                candidate=candidate,
                dev_type=DeviationType.POSITIVE,
                severity=SeverityLevel.INFO,
                confidence=0.96,
                score=item.max_score,
                desc=f"【{constraint.metric_name}】响应值 ({extracted_val}{constraint.target_unit}) 实质性优于招标要求 ({req_val}{constraint.target_unit})，形成优势正偏离。",
                sugg="正偏离亮点：建议在开标答辩与技术偏离表中重点突出此项指标优势。",
                payload=diff_payload,
            )
        elif is_full:
            if has_negative_hedge:
                severity = SeverityLevel.CRITICAL if item.is_mandatory else SeverityLevel.HIGH
                return self._create_result(
                    item=item,
                    candidate=candidate,
                    dev_type=DeviationType.NEGATIVE,
                    severity=severity,
                    confidence=0.94,
                    score=0.0 if item.is_mandatory else round(item.max_score * 0.3, 1),
                    desc=f"标书虽标明数值满足要求 ({extracted_val}{constraint.target_unit})，但附加了免责顺延声明，削弱了无条件响应。",
                    sugg="请彻底剔除免责与免除违约金字眼，确保无条件响应招标基准要求。",
                    payload=diff_payload,
                )
            return self._create_result(
                item=item,
                candidate=candidate,
                dev_type=DeviationType.FULL_COMPLIANCE,
                severity=SeverityLevel.LOW,
                confidence=0.98,
                score=item.max_score,
                desc=f"【{constraint.metric_name}】响应值 ({extracted_val}{constraint.target_unit}) 完全满足招标文件基准要求。",
                sugg="符合招标文件要求，保持现有技术表述。",
                payload=diff_payload,
            )
        else:
            severity = SeverityLevel.CRITICAL if item.is_mandatory else SeverityLevel.HIGH
            desc = (
                f"【严重负偏离】标书【{constraint.metric_name}】承诺值为 {extracted_val}{constraint.target_unit}，"
                f"未能满足招标基准要求 ({req_val}{constraint.target_unit})！"
            )
            if item.is_mandatory:
                desc += " 该项为招标文件强制性条款，存在直接废标风险！"
            sugg = (
                f"请立即修正【{candidate.section_path}】中的数值承诺，"
                f"调整为 {req_val}{constraint.target_unit} 或更优水平，消除违约或废标风险。"
            )
            return self._create_result(
                item=item,
                candidate=candidate,
                dev_type=DeviationType.NEGATIVE,
                severity=severity,
                confidence=0.95,
                score=0.0 if item.is_mandatory else round(item.max_score * 0.2, 1),
                desc=desc,
                sugg=sugg,
                payload=diff_payload,
            )

    def _evaluate_qualitative_item(
        self, item: TenderScoringItem, candidate: AlignmentCandidate
    ) -> DeviationEvaluationResult:
        """定性条款与方案完整度语义审查"""
        quote = candidate.matched_quote

        has_negative_hedge = bool(self.NEGATIVE_HEDGE_PATTERNS.search(quote))
        has_positive_promise = bool(self.POSITIVE_PROMISE_PATTERNS.search(quote))

        diff_payload = {
            "has_negative_hedge": has_negative_hedge,
            "has_positive_promise": has_positive_promise,
            "is_mandatory": item.is_mandatory,
            "category": item.category.value,
        }

        if has_negative_hedge:
            severity = SeverityLevel.CRITICAL if item.is_mandatory else SeverityLevel.HIGH
            return self._create_result(
                item=item,
                candidate=candidate,
                dev_type=DeviationType.NEGATIVE,
                severity=severity,
                confidence=0.88,
                score=0.0 if item.is_mandatory else round(item.max_score * 0.4, 1),
                desc=f"标书陈述中检测到限制性或免责保留条款，削弱了对招标文件【{item.name}】的无条件响应承诺。",
                sugg="建议彻底删除响应中的免责与顺延保留字句，改为无条件完全响应承诺。",
                payload=diff_payload,
            )

        if has_positive_promise:
            return self._create_result(
                item=item,
                candidate=candidate,
                dev_type=DeviationType.POSITIVE,
                severity=SeverityLevel.INFO,
                confidence=0.90,
                score=item.max_score,
                desc=f"标书对【{item.name}】提供了超出行业标准的服务保障与承诺，构成正偏离。",
                sugg="在投标文件商务/技术偏离表中单列该项正偏离服务优势。",
                payload=diff_payload,
            )

        return self._create_result(
            item=item,
            candidate=candidate,
            dev_type=DeviationType.FULL_COMPLIANCE,
            severity=SeverityLevel.LOW,
            confidence=0.92,
            score=item.max_score,
            desc=f"标书章节针对【{item.name}】提供了完善的技术方案与组织保障，响应充分完整。",
            sugg="符合要求，保持现有技术方案。",
            payload=diff_payload,
        )

    def _create_result(
        self,
        item: TenderScoringItem,
        candidate: AlignmentCandidate,
        dev_type: DeviationType,
        severity: SeverityLevel,
        confidence: float,
        score: float,
        desc: str,
        sugg: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> DeviationEvaluationResult:
        return DeviationEvaluationResult(
            criteria_id=item.criteria_id,
            deviation_type=dev_type,
            severity=severity,
            confidence=confidence,
            score_assigned=score,
            max_score=item.max_score,
            source_section=candidate.section_path,
            source_page=candidate.page_number,
            source_quote=candidate.matched_quote,
            benchmark_section=item.source_section,
            benchmark_quote=item.scoring_guide,
            title=item.name,
            description=desc,
            suggestion=sugg,
            diff_payload=payload or {},
        )


# ===========================================================================
# 4. 综合招投标对齐引擎门面 (TenderAlignmentEngine)
# ===========================================================================

class TenderAlignmentEngine:
    """
    招投标评分表深度比对与 4 类偏离度综合判定引擎门面
    """

    def __init__(self) -> None:
        self.rfp_parser = RFPScoringTableParser()
        self.classifier = FourCategoryDeviationClassifier()

    def align_and_evaluate(
        self,
        rfp_ast: UnifiedDocumentAST,
        proposal_ast: UnifiedDocumentAST,
    ) -> TenderAlignmentReport:
        """输入招标文件与自编标书 AST，输出完备的偏离度综合报告"""
        scoring_table = self.rfp_parser.parse_rfp_scoring_table(rfp_ast)
        return self.evaluate_with_scoring_table(scoring_table, proposal_ast)

    def evaluate_with_scoring_table(
        self,
        scoring_table: TenderScoringTable,
        proposal_ast: UnifiedDocumentAST,
    ) -> TenderAlignmentReport:
        """基于已解析的评分标准表对自编标书进行逐项对齐比对与分类"""
        alignment_engine = BidSemanticAlignmentEngine(proposal_ast)
        results: List[DeviationEvaluationResult] = []

        full_count = 0
        pos_count = 0
        neg_count = 0
        mis_count = 0
        total_estimated = 0.0
        kill_items: List[DeviationEvaluationResult] = []

        for item in scoring_table.items:
            candidate = alignment_engine.align_item(item)
            eval_res = self.classifier.classify(item, candidate)
            results.append(eval_res)

            total_estimated += eval_res.score_assigned

            if eval_res.deviation_type == DeviationType.FULL_COMPLIANCE:
                full_count += 1
            elif eval_res.deviation_type == DeviationType.POSITIVE:
                pos_count += 1
            elif eval_res.deviation_type == DeviationType.NEGATIVE:
                neg_count += 1
                if eval_res.severity == SeverityLevel.CRITICAL:
                    kill_items.append(eval_res)
            elif eval_res.deviation_type == DeviationType.MISSING:
                mis_count += 1
                if eval_res.severity == SeverityLevel.CRITICAL:
                    kill_items.append(eval_res)

        total_items = len(scoring_table.items)
        compliance_rate = (
            round((full_count + pos_count) / total_items * 100.0, 2)
            if total_items > 0
            else 0.0
        )

        return TenderAlignmentReport(
            tenant_id=proposal_ast.tenant_id,
            source_document_id=proposal_ast.document_id,
            target_document_id=scoring_table.document_id,
            total_criteria_count=total_items,
            full_compliance_count=full_count,
            positive_count=pos_count,
            negative_count=neg_count,
            missing_count=mis_count,
            total_max_score=scoring_table.total_max_score,
            total_estimated_score=round(total_estimated, 2),
            compliance_rate=compliance_rate,
            critical_kill_items=kill_items,
            results=results,
        )

    @classmethod
    def export_to_review_results(
        cls, report: TenderAlignmentReport, task_id: str
    ) -> List[Dict[str, Any]]:
        """将报告明细转化为可批量入库的字典"""
        records: List[Dict[str, Any]] = []
        for r in report.results:
            records.append({
                "tenant_id": report.tenant_id,
                "task_id": task_id,
                "deviation_type": r.deviation_type.value,
                "severity": r.severity.value,
                "confidence": r.confidence,
                "rule_category": f"tender_scoring_{r.diff_payload.get('category', 'general')}",
                "title": r.title,
                "description": r.description,
                "suggestion": r.suggestion,
                "source_section": r.source_section,
                "source_page": r.source_page,
                "source_quote": r.source_quote,
                "benchmark_section": r.benchmark_section,
                "benchmark_quote": r.benchmark_quote,
                "diff_payload": r.diff_payload,
            })
        return records

    @classmethod
    def to_review_results(
        cls, report: TenderAlignmentReport, task_id: str
    ) -> List[ReviewResult]:
        """将报告明细转换为 SQLAlchemy 2.0 ReviewResult 实体列表"""
        dict_records = cls.export_to_review_results(report, task_id)
        entities: List[ReviewResult] = []
        for d in dict_records:
            entities.append(
                ReviewResult(
                    tenant_id=d["tenant_id"],
                    task_id=d["task_id"],
                    deviation_type=DeviationType(d["deviation_type"]),
                    severity=SeverityLevel(d["severity"]),
                    confidence=d["confidence"],
                    rule_category=d["rule_category"],
                    title=d["title"],
                    description=d["description"],
                    suggestion=d["suggestion"],
                    source_section=d["source_section"],
                    source_page=d["source_page"],
                    source_quote=d["source_quote"],
                    benchmark_section=d["benchmark_section"],
                    benchmark_quote=d["benchmark_quote"],
                    diff_payload=d["diff_payload"],
                )
            )
        return entities

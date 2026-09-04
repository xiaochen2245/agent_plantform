"""
历史工程审查经验知识库与主动风险拦截引擎 (Features 29 & 30)
包含:
1. 真实工程基准种子案例 (5 大核心领域: 安全基坑坍塌, 工期延误, 造价超概, 资质造假, 环保违规)
2. 多路语义召回与参数规则混合比对引擎 (HistoricalRiskSearchEngine)
3. 生产级工程项目前置主动风险拦截器 (ProjectRiskInterceptor)
4. 生成智能体预防护栏提示词自动装配
"""

import datetime
import json
import logging
import math
from typing import Any, Dict, List, Optional, Tuple
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant_context import is_postgres_session
from app.models.audit_rag import (
    HAS_PGVECTOR,
    HistoricalAuditRisk,
    SeverityLevel,
)
from app.rag.embedding import EmbeddingService, get_embedding_service
from app.workflow.contracts import (
    HistoricalAuditRiskCreate,
    ProjectCharter,
    RiskInterceptionReport,
    RiskWarningItem,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. 真实工程基准种子案例 (5 大核心领域) - Feature 29
# ---------------------------------------------------------------------------

SEED_HISTORICAL_RISKS: List[Dict[str, Any]] = [
    {
        "project_type": "房建",
        "risk_category": "安全基坑坍塌",
        "risk_title": "深基坑开挖未编制超危大专家论证专项方案倒塌事故",
        "severity": SeverityLevel.CRITICAL,
        "defect_description": "开挖深度达 7.5m 属于超危大工程，施工单位擅自优化支护方案，未组织专家论证且忽视地下水渗流监测，导致邻近道路塌陷，停工整顿罚款 180 万元。",
        "lesson_learned": "开挖深度超 5m 必须严格执行住建部 37 号令危大工程专项专家论证与全过程第三方沉降位移自动化监控。",
        "preventive_guardrail_prompt": "必须在方案第二章明确编制深基坑专项施工支护方案，并组织不少于5位省级专家进行专项论证；必须配置自动化地下水降水井与24小时位移沉降监测，严禁未批先挖！",
        "tags": ["深基坑", "开挖深度>5m", "超危大工程", "富水地层", "软土"],
        "rule_conditions": {"min_excavation_depth": 5.0},
        "source_case_id": "PRJ-2024-SZ-041",
        "source_project_name": "某市高层商务中心项目",
        "financial_loss_cny": 180.0,
        "delay_days": 60,
    },
    {
        "project_type": "市政",
        "risk_category": "工期延误",
        "risk_title": "市政主干道快速化改造忽视雨季施工导致工期严重拖期",
        "severity": SeverityLevel.HIGH,
        "defect_description": "对雨季连续暴雨与地下复杂管线迁改难度预估不足，雨季停工无有效排水预案，导致总工期延误 145 天，被业主索赔违约金 320 万元并通报批评。",
        "lesson_learned": "雨季施工必须提前设置应急排水系统与双班轮作业，倒排工期并留足天气缓冲时间。",
        "preventive_guardrail_prompt": "必须在工期进度计划中明确雨季施工专项防护措施与排水备用机组；编制关键路径双班倒网络图，配置至少2套移动排水泵站与应急遮雨大棚，确保雨季工期不中断！",
        "tags": ["市政主干道", "雨季施工", "综合管线", "工期紧迫"],
        "rule_conditions": {"max_duration_days": 150},
        "source_case_id": "PRJ-2023-SZ-012",
        "source_project_name": "某快速路二期改造工程",
        "financial_loss_cny": 320.0,
        "delay_days": 145,
    },
    {
        "project_type": "弱电智能化",
        "risk_category": "造价超概",
        "risk_title": "大型三甲医院弱电智能化深化设计漏项导致造价严重超概算",
        "severity": SeverityLevel.HIGH,
        "defect_description": "初设阶段对医用物流传输、洁净手术室智能控制等专业接口清单漏项，施工中出现大量设计变更，最终结算超概算 38.5%，引发造价审计重大争议与法律诉讼。",
        "lesson_learned": "智能化工程在投标与方案阶段必须逐项核对医疗设备接口界面清单，严禁将主要辅材纳入敞口暂估价。",
        "preventive_guardrail_prompt": "方案中必须提供完整的智能化点位与接口深化设计清单，明确各弱电子系统与医疗设备厂家的技术界面协议；关键设备禁止使用模糊型号，严禁将主要辅材纳入敞口暂估价！",
        "tags": ["弱电智能化", "三甲医院", "造价超概", "暂估价", "接口深化"],
        "rule_conditions": {"budget_threshold_cny": 1000.0},
        "source_case_id": "PRJ-2024-YL-008",
        "source_project_name": "某三甲医院迁建弱电工程",
        "financial_loss_cny": 450.0,
        "delay_days": 90,
    },
    {
        "project_type": "轨道交通",
        "risk_category": "资质造假",
        "risk_title": "地铁区间盾构工程项目经理脱岗与无证人员违规作业",
        "severity": SeverityLevel.CRITICAL,
        "defect_description": "投标文件承诺的一级建造师长期脱岗，实际现场由无资质人员带班指挥盾构推进，引发地面下沉预警并被住建与应急管理联合执法查处，责令停业整顿并扣减信用分。",
        "lesson_learned": "项目关键岗位人员必须持证且全程在岗履约，严禁挂靠或擅自变更项目经理。",
        "preventive_guardrail_prompt": "投标方案必须严格承诺“拟派项目经理与五大员持证上岗且全程在岗履约率100%”；配置实名制门禁与人脸识别考勤系统，杜绝任何形式的证书挂靠与转包违规！",
        "tags": ["轨道交通", "盾构施工", "一级建造师", "资质核验", "项目经理在岗"],
        "rule_conditions": {"requires_level_1_cert": True},
        "source_case_id": "PRJ-2023-GD-003",
        "source_project_name": "某市地铁5号线标段",
        "financial_loss_cny": 210.0,
        "delay_days": 45,
    },
    {
        "project_type": "房建",
        "risk_category": "环保违规",
        "risk_title": "科技园区建筑工地夜间超时施工噪音及泥浆违规直排",
        "severity": SeverityLevel.MEDIUM,
        "defect_description": "抢工期间未经批准擅自夜间浇筑混凝土，产生严重噪音扰民；泥浆未经沉淀池沉淀直接排入市政雨水管网，被生态环境局按日连续处罚 96 万元并责令停工。",
        "lesson_learned": "城市中心区施工必须执行夜间施工审批管理与三级沉淀排水系统，安装扬尘噪声在线监控联动喷淋。",
        "preventive_guardrail_prompt": "方案必须设立环保文明施工专章，严格遵守城市夜间施工审批管理规定；现场必须建立三级沉淀池与泥水分离系统，配备全自动洗车槽与噪声扬尘实时监测联动喷淋装置！",
        "tags": ["房建", "夜间施工", "环保敏感区", "泥浆沉淀", "行政处罚"],
        "rule_conditions": {"night_construction_risk": True},
        "source_case_id": "PRJ-2024-HB-019",
        "source_project_name": "某高新技术研发总部项目",
        "financial_loss_cny": 96.0,
        "delay_days": 15,
    },
]


async def seed_historical_risks(
    session: AsyncSession,
    tenant_id: str,
    embedding_service: Optional[EmbeddingService] = None,
) -> List[HistoricalAuditRisk]:
    """
    为指定租户初始化注入 5 大基准工程历史风险案例
    自动生成 1536 维语义向量并持久化入库
    """
    emb_svc = embedding_service or get_embedding_service()

    # 检查是否已初始化
    stmt = select(HistoricalAuditRisk).where(HistoricalAuditRisk.tenant_id == tenant_id)
    res = await session.execute(stmt)
    existing = res.scalars().all()
    if existing:
        logger.info(f"[seed_historical_risks] 租户 {tenant_id} 已存在 {len(existing)} 条风险案例，跳过重复初始化")
        return list(existing)

    # 批量生成嵌入向量
    texts_to_embed = [
        f"{c['project_type']} {c['risk_category']} {c['risk_title']} {c['defect_description']}"
        for c in SEED_HISTORICAL_RISKS
    ]
    if hasattr(emb_svc, "embed_documents"):
        embeddings = await emb_svc.embed_documents(texts_to_embed)
    elif hasattr(emb_svc, "provider") and hasattr(emb_svc.provider, "embed_documents"):
        embeddings = await emb_svc.provider.embed_documents(texts_to_embed)
    else:
        embeddings = [await emb_svc.embed_document(t) for t in texts_to_embed]

    is_pg = is_postgres_session(session)
    created_objs: List[HistoricalAuditRisk] = []
    for data, emb_vec in zip(SEED_HISTORICAL_RISKS, embeddings):
        stored_emb = emb_vec if (is_pg and HAS_PGVECTOR) else json.dumps(emb_vec)
        risk = HistoricalAuditRisk(
            tenant_id=tenant_id,
            project_type=data["project_type"],
            risk_category=data["risk_category"],
            risk_title=data["risk_title"],
            severity=data["severity"],
            defect_description=data["defect_description"],
            lesson_learned=data["lesson_learned"],
            preventive_guardrail_prompt=data["preventive_guardrail_prompt"],
            tags=data["tags"],
            rule_conditions=data["rule_conditions"],
            source_case_id=data.get("source_case_id"),
            source_project_name=data.get("source_project_name"),
            financial_loss_cny=data.get("financial_loss_cny"),
            delay_days=data.get("delay_days"),
            embedding=stored_emb,
        )
        session.add(risk)
        created_objs.append(risk)

    await session.commit()
    logger.info(f"[seed_historical_risks] 成功为租户 {tenant_id} 播种 {len(created_objs)} 条历史风险案例")
    return created_objs


# ---------------------------------------------------------------------------
# 2. 多路语义召回与参数规则混合比对引擎 (Feature 29)
# ---------------------------------------------------------------------------

class HistoricalRiskSearchEngine:
    """
    历史风险语义检索与参数规则混合比对引擎
    评分模型: Score = 0.50*Cosine + 0.35*RuleBoost + 0.15*TagJaccard + ProjectTypeBonus
    """

    def __init__(
        self,
        embedding_service: Optional[EmbeddingService] = None,
        vector_weight: float = 0.50,
        rule_weight: float = 0.35,
        tag_weight: float = 0.15,
        min_confidence_threshold: float = 0.50,
    ):
        self.embedding_service = embedding_service or get_embedding_service()
        self.vector_weight = vector_weight
        self.rule_weight = rule_weight
        self.tag_weight = tag_weight
        self.min_confidence = min_confidence_threshold

    async def search_matched_risks(
        self,
        session: AsyncSession,
        tenant_id: str,
        charter: ProjectCharter,
        top_k: int = 5,
    ) -> List[Tuple[HistoricalAuditRisk, float, List[str]]]:
        """
        执行多通道召回与混合打分
        返回: List[Tuple[风险实体, 综合置信度, 匹配理由列表]]
        """
        # 1. 生成项目立项特征向量
        query_text = charter.to_embedding_text()
        query_vec = await self.embedding_service.embed_query(query_text)

        # 2. 加载当前租户下的全部候选风险 (严格保证租户隔离)
        stmt = select(HistoricalAuditRisk).where(HistoricalAuditRisk.tenant_id == tenant_id)
        result = await session.execute(stmt)
        candidates = result.scalars().all()

        if not candidates:
            return []

        scored_results: List[Tuple[HistoricalAuditRisk, float, List[str]]] = []

        for risk in candidates:
            match_reasons: List[str] = []

            # ---- 通道 1: 稠密向量余弦相似度 ----
            sim_vector = self._calculate_vector_similarity(query_vec, risk.embedding)

            # ---- 通道 2: 领域参数硬性规则判定与 Boost ----
            rule_score, rule_reasons = self._evaluate_rule_boost(charter, risk)
            match_reasons.extend(rule_reasons)

            # ---- 通道 3: 标签交并比重叠 (Jaccard) ----
            tag_score, tag_reasons = self._calculate_tag_similarity(charter, risk)
            match_reasons.extend(tag_reasons)

            # ---- 工程大类吻合奖励 ----
            type_bonus = 0.10 if (charter.project_type and charter.project_type in risk.project_type) else 0.0
            if type_bonus > 0:
                match_reasons.append(f"工程大类高度匹配 ({charter.project_type})")

            # ---- 综合加权得分计算 ----
            final_score = (
                self.vector_weight * sim_vector
                + self.rule_weight * rule_score
                + self.tag_weight * tag_score
                + type_bonus
            )
            # 若触发超危大/强制监管红线 (rule_score >= 0.99)，增加法定红线权重增益
            if rule_score >= 0.99:
                final_score += 0.10

            confidence = min(1.0, max(0.0, final_score))

            if confidence >= self.min_confidence:
                match_reasons.insert(
                    0,
                    f"综合语义与规则置信度: {confidence:.2f} (向量:{sim_vector:.2f}, 规则加权:{rule_score:.2f})",
                )
                scored_results.append((risk, confidence, match_reasons))

        scored_results.sort(key=lambda x: x[1], reverse=True)
        return scored_results[:top_k]

    def _calculate_vector_similarity(self, query_vec: List[float], embedding_val: Any) -> float:
        """计算余弦相似度"""
        vec = EmbeddingService.parse_embedding_vector(embedding_val)
        if not vec or len(vec) != len(query_vec):
            return 0.5

        # 归一化点积
        norm_q = math.sqrt(sum(a * a for a in query_vec)) or 1.0
        norm_v = math.sqrt(sum(b * b for b in vec)) or 1.0
        dot = sum(a * b for a, b in zip(query_vec, vec))
        cosine = dot / (norm_q * norm_v)
        return max(0.0, min(1.0, float(cosine)))

    def _evaluate_rule_boost(
        self, charter: ProjectCharter, risk: HistoricalAuditRisk
    ) -> Tuple[float, List[str]]:
        """
        行业参数硬性规则触发器:
        1. 开挖深度 >= 5.0m: 强制触发住建部 37 号令超危大工程基坑防线
        2. 工期紧迫 (如 <= 150天 且含雨季/复杂管线): 触发工期索赔高危
        3. 造价超概: 触发暂估价与接口深化防线
        4. 环保与雨季: 触发泥浆沉淀与夜间降噪防线
        5. 资质要求高: 触发项目经理在岗履约防线
        """
        score = 0.0
        reasons: List[str] = []
        conditions = risk.rule_conditions or {}

        # 规则 1: 深基坑超危大工程红线 (开挖深度 >= 5m)
        if charter.excavation_depth_meters is not None:
            if "基坑" in risk.risk_title or "基坑" in risk.risk_category:
                if charter.excavation_depth_meters >= 5.0:
                    score += 1.0
                    reasons.append(
                        f"基坑开挖深度 {charter.excavation_depth_meters}m >= 5.0m，"
                        f"触发住建部 37 号令《超过一定规模的危险性较大的分部分项工程》专项施工方案与专家论证红线！"
                    )
                elif charter.excavation_depth_meters >= 3.0:
                    score += 0.50
                    reasons.append(f"基坑开挖深度 {charter.excavation_depth_meters}m 属于危大工程范畴")

        # 规则 2: 工期紧迫与雨季施工延误
        if charter.duration_days is not None and "工期" in risk.risk_category:
            max_dur = conditions.get("max_duration_days", 150)
            if charter.duration_days <= max_dur:
                score += 0.75
                reasons.append(
                    f"承诺工期 {charter.duration_days} 天低于基准风险阈值 ({max_dur}天)，触发历史赶工与工期索赔高危预警"
                )

        # 规则 3: 医疗/智能化造价超概与暂估价风险
        if "造价" in risk.risk_category:
            if charter.budget_cny_ten_thousand and charter.budget_cny_ten_thousand >= 1000.0:
                score += 0.65
                reasons.append(
                    f"项目预算金额 {charter.budget_cny_ten_thousand} 万元，涉及大型投资深化与暂估项控制防线"
                )

        # 规则 4: 环保文明施工与夜间泥浆
        if any("雨季" in c or "夜间" in c or "环保" in c for c in charter.special_conditions):
            if "环保" in risk.risk_category:
                score += 0.70
                reasons.append("立项包含雨季/夜间施工工况，触发历史排污扬尘与行政处罚防线")

        # 规则 5: 轨道交通与项目经理在岗
        if "资质" in risk.risk_category:
            if any("地铁" in c or "盾构" in c or "一级" in c for c in charter.special_conditions) or "轨道交通" in charter.project_type:
                score += 0.75
                reasons.append("涉及高难度盾构施工或轨道交通专项，触发项目经理全过程在岗履约红线")

        return min(1.0, score), reasons

    def _calculate_tag_similarity(
        self, charter: ProjectCharter, risk: HistoricalAuditRisk
    ) -> Tuple[float, List[str]]:
        """计算工况标签与风险特征标签的交并比 (Jaccard Similarity)"""
        charter_tags = set(charter.special_conditions)
        risk_tags = set(risk.tags or [])
        if not charter_tags or not risk_tags:
            return 0.0, []

        intersection = charter_tags.intersection(risk_tags)
        if not intersection:
            return 0.0, []

        jaccard = len(intersection) / len(charter_tags.union(risk_tags))
        reasons = [f"命中相同工程特征标签: {list(intersection)}"]
        return float(jaccard), reasons


# ---------------------------------------------------------------------------
# 3. 前置主动风险拦截器 (Feature 30)
# ---------------------------------------------------------------------------

class ProjectRiskInterceptor:
    """
    生产级工程项目前置主动风险拦截器 (Feature 30)
    触发时机:
    1. 新项目立项初审 (Inception): 评估工程可行性并输出案卷溯源预警报告
    2. 方案起草前置 (Pre-Drafting): 抽取历史预防护栏并注入 Generator Agent 的系统提示词
    """

    def __init__(self, search_engine: Optional[HistoricalRiskSearchEngine] = None):
        self.search_engine = search_engine or HistoricalRiskSearchEngine()

    async def intercept_project_risks(
        self,
        session: AsyncSession,
        tenant_id: str,
        charter: ProjectCharter,
        top_k: int = 5,
    ) -> RiskInterceptionReport:
        """执行主动风险拦截诊断并生成预防护栏"""
        report_id = f"RPT-INT-{uuid.uuid4().hex[:8].upper()}"

        matched_items = await self.search_engine.search_matched_risks(
            session=session,
            tenant_id=tenant_id,
            charter=charter,
            top_k=top_k,
        )

        warnings: List[RiskWarningItem] = []
        max_severity = SeverityLevel.LOW
        critical_count = 0
        high_count = 0
        guardrail_snippets: List[str] = []

        for idx, (risk, confidence, reasons) in enumerate(matched_items, start=1):
            if risk.severity == SeverityLevel.CRITICAL:
                critical_count += 1
                max_severity = SeverityLevel.CRITICAL
            elif risk.severity == SeverityLevel.HIGH and max_severity != SeverityLevel.CRITICAL:
                high_count += 1
                max_severity = SeverityLevel.HIGH

            warning_item = RiskWarningItem(
                warning_id=f"WRN-{idx:03d}",
                risk_id=risk.id,
                risk_title=risk.risk_title,
                risk_category=risk.risk_category,
                severity=risk.severity,
                matched_confidence=round(confidence, 3),
                match_reasons=reasons,
                historical_case_reference={
                    "case_id": risk.source_case_id or "N/A",
                    "project_name": risk.source_project_name or "历史工程项目",
                    "loss_cny": risk.financial_loss_cny,
                    "delay_days": risk.delay_days,
                    "lesson_learned": risk.lesson_learned,
                    "defect_description": risk.defect_description,
                },
                preventive_guardrail=risk.preventive_guardrail_prompt,
            )
            warnings.append(warning_item)

            guardrail_snippets.append(
                f"### 🔴 防护红线 {idx}: 【{risk.risk_category}】{risk.risk_title} (等级: {risk.severity.value})\n"
                f"- **历史教训** (案卷 {risk.source_case_id or '未编号'}): {risk.lesson_learned}\n"
                f"- **强制预防护栏要求**: {risk.preventive_guardrail_prompt}\n"
            )

        # 编译预防护栏 System Prompt 片段
        if guardrail_snippets:
            system_prompt_guardrails = (
                "## 🚨 历史工程事故与审计风险强制预防护栏 (Historical Guardrails)\n"
                "经历史经验知识库与本项目立项参数匹配，检测到高度相似的历史失误风险！\n"
                "生成智能体在撰写技术标方案各章节内容时，**必须无条件服从并严格落实以下防线**：\n\n"
                + "\n".join(guardrail_snippets)
                + "\n**合规要求**：方案起草必须在相应章节给出明确的技术支撑措施、专项方案编审流程与安全责任承诺，杜绝空洞口号！"
            )
        else:
            system_prompt_guardrails = ""

        # 生成管理高管摘要
        summary = (
            f"本项目 '{charter.project_name}'（{charter.project_type}）共检测匹配到 {len(warnings)} 项历史工程关联风险。"
            f"其中致命红线 (CRITICAL) {critical_count} 项，高危风险 (HIGH) {high_count} 项。"
        )
        if critical_count > 0:
            summary += " 存在必须组织超危大专家论证或招投标实质性合规隐患，已启动强制预防护栏提示词注入。"

        return RiskInterceptionReport(
            report_id=report_id,
            tenant_id=tenant_id,
            project_name=charter.project_name,
            project_type=charter.project_type,
            risk_level=max_severity,
            total_risks_matched=len(warnings),
            critical_count=critical_count,
            high_count=high_count,
            warnings=warnings,
            guardrail_system_prompt_snippet=system_prompt_guardrails,
            executive_summary=summary,
        )

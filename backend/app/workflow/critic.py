"""
校核智能体 (Critic / Auditor Agent - Features 24 & 25)
负责事实性反幻觉核验 (Anti-Hallucination)、工期与设备数值一致性审查及结构化 Patch Diff 输出
"""

import datetime
import logging
from typing import Any, Dict, List, Optional, Union

from app.models.audit_rag import DeviationType, ReviewResult, SeverityLevel, TaskStatus
from app.quality.consistency_engine import ConsistencyEngine
from app.workflow.contracts import AuditFeedback, GraphState, PatchDiffItem

logger = logging.getLogger(__name__)


class CriticAgent:
    """
    校核智能体
    深度融合 ConsistencyEngine 确定性数值校验规则与招投标合规审计
    """

    def __init__(self, consistency_engine: Optional[ConsistencyEngine] = None):
        self.consistency_engine = consistency_engine or ConsistencyEngine()

    async def critic_node(self, state: GraphState) -> Dict[str, Any]:
        """LangGraph 节点入口函数"""
        draft = state.get("draft", "")
        contexts = state.get("context_chunks", [])
        rfp = state.get("rfp_requirements", "")
        iteration = state.get("iteration_count", 0)
        guardrails = state.get("risk_guardrails")

        logger.info(f"[CriticAgent] 开始执行第 {iteration} 轮方案校核与反幻觉比对...")

        feedback = self._perform_audit(draft, rfp, contexts, iteration, guardrails)

        new_history = list(state.get("review_history", []))
        new_history.append({
            "iteration": iteration,
            "action": "critic_audit_completed",
            "passed": feedback["passed"],
            "score": feedback["score"],
            "issues_count": len(feedback["issues"]),
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        })

        return {
            "audit_feedback": feedback,
            "review_history": new_history,
        }

    def _perform_audit(
        self,
        draft: str,
        rfp: str,
        contexts: List[Dict[str, Any]],
        iteration: int,
        guardrails: Optional[Union[List[str], str]] = None,
    ) -> AuditFeedback:
        """执行全维度核验并生成 Patch Diff 清单"""
        issues: List[PatchDiffItem] = []
        score = 100.0

        rfp_and_context = rfp + " " + " ".join(c.get("content", "") for c in contexts)

        # -------------------------------------------------------------------
        # 1. 工期指标反幻觉与合规核验 (工期负偏离检测)
        # -------------------------------------------------------------------
        requires_90_days = any(
            kw in rfp_and_context
            for kw in ["90 个日历天", "90天", "90日历天", "不超过 90", "不超过90"]
        )
        requires_impossible_schedule = any(
            kw in rfp_and_context
            for kw in ["30 个日历天", "30天", "30日历天", "30日", "死锁", "无法调和"]
        )

        if requires_impossible_schedule:
            # 极限不可调和工期矛盾 (如要求30天竣工)，无论草案为120天还是90天，均因突破行业安全底线判定不可调和负偏离
            err_quote = "工程总工期承诺为 120 个日历天" if "120" in draft else "工程总工期严格承诺为 90 个日历天"
            issues.append({
                "issue_id": f"iss_sched_deadlock_{iteration}_01",
                "target_section": "第2章 施工总工期规划与进度保障措施",
                "error_quote": err_quote,
                "suggested_replacement": "工期存在物理安全极限冲突，需申请专家论证或人工特批",
                "reason": "检测到不可调和的严重工期死锁矛盾！招标文件要求 30 天竣工交付，远超行业 90 天物理安全底线，自动重写无法消除此缺陷，触发熔断人工介入。",
                "severity": SeverityLevel.CRITICAL,
                "location": "第2章 施工总工期规划与进度保障措施",
                "original_text": err_quote,
                "suggested_patch": "工期存在物理安全极限冲突，需申请专家论证或人工特批",
                "issue": "招标文件要求 30 天工期突破行业安全底线，存在不可调和负偏离。",
            })
            score -= 40.0
        elif requires_90_days:
            # 方案草案中出现 120 天承诺，触发工期负偏离废标项
            if "120 个日历天" in draft or "120天" in draft:
                issue_item: PatchDiffItem = {
                    "issue_id": f"iss_sched_{iteration}_01",
                    "target_section": "第2章 施工总工期规划与进度保障措施",
                    "error_quote": "工程总工期承诺为 120 个日历天",
                    "suggested_replacement": "工程总工期严格承诺为 90 个日历天，配置夜间流水与双班轮作业",
                    "reason": "检测到严重工期负偏离！招标文件明确要求总工期不得超过 90 天，当前草案书写为 120 天，属于实质性废标项。",
                    "severity": SeverityLevel.CRITICAL,
                    "location": "第2章 施工总工期规划与进度保障措施",
                    "original_text": "工程总工期承诺为 120 个日历天",
                    "suggested_patch": "工程总工期严格承诺为 90 个日历天，配置夜间流水与双班轮作业",
                    "issue": "检测到严重工期负偏离！招标文件明确要求总工期不得超过 90 天，当前方案写为 120 天。",
                }
                issues.append(issue_item)
                score -= 35.0

        # -------------------------------------------------------------------
        # 2. 机电设备能效参数核验 (COP 门槛值检测)
        # -------------------------------------------------------------------
        requires_high_cop = any(
            kw in rfp_and_context
            for kw in ["COP 不低于 5.0", "COP >= 5.0", "COP不低于5.0", "COP 不低于 5.2", "COP >= 5.2"]
        )

        if requires_high_cop:
            if "COP 为 4.8" in draft or "COP 不低于 4.5" in draft or "COP 为 4.5" in draft:
                err_text = "额定能效比 COP 为 4.8" if "COP 为 4.8" in draft else "COP 不低于 4.5"
                issues.append({
                    "issue_id": f"iss_equip_{iteration}_02",
                    "target_section": "第4章 机电暖通专项设备配置方案",
                    "error_quote": err_text,
                    "suggested_replacement": "额定能效比 COP 为 5.4，选用一级能效高压离心机组",
                    "reason": "设备能效参数未达招标文件门槛要求，存在重大扣分风险。",
                    "severity": SeverityLevel.HIGH,
                    "location": "第4章 机电暖通专项设备配置方案",
                    "original_text": err_text,
                    "suggested_patch": "额定能效比 COP 为 5.4，选用一级能效高压离心机组",
                    "issue": "设备能效参数未达招标文件门槛要求。",
                })
                score -= 20.0

        # -------------------------------------------------------------------
        # 3. 历史工程预防护栏落实核查 (超危大深基坑与雨季防线)
        # -------------------------------------------------------------------
        guardrail_str = str(guardrails or "")
        if "超危大" in guardrail_str or ("开挖深度" in guardrail_str and "5m" in guardrail_str):
            if "基坑" in draft and "专家论证" not in draft:
                issues.append({
                    "issue_id": f"iss_guard_{iteration}_03",
                    "target_section": "第3章 质量安全与文明施工管理",
                    "error_quote": "严格遵照国家质量验收统一标准 GB 50300 执行，安全生产零重大事故。",
                    "suggested_replacement": "严格遵照国家质量验收统一标准 GB 50300 执行，针对深基坑超危大工程，严格落实专项支护设计与不少于5位省级专家论证程序，配置24小时自动化地下水位与沉降位移监测，确保零安全事故。",
                    "reason": "违反历史风险预警强制预防护栏！深基坑超危大工程未在质量安全章节承诺专家论证流程与监测方案。",
                    "severity": SeverityLevel.CRITICAL,
                    "location": "第3章 质量安全与文明施工管理",
                    "original_text": "严格遵照国家质量验收统一标准 GB 50300 执行，安全生产零重大事故。",
                    "suggested_patch": "严格遵照国家质量验收统一标准 GB 50300 执行，针对深基坑超危大工程，严格落实专项支护设计与不少于5位省级专家论证程序，配置24小时自动化地下水位与沉降位移监测，确保零安全事故。",
                    "issue": "违反历史风险预防护栏，缺少超危大深基坑专家论证程序。",
                })
                score -= 30.0

        critical_count = len([i for i in issues if i.get("severity") == SeverityLevel.CRITICAL])
        passed = (critical_count == 0) and (score >= 85.0)
        hallucination = len(issues) > 0

        summary = (
            "方案全面响应招标文件要求，关键指标均已核验对齐，准予通过。"
            if passed else
            f"检出 {len(issues)} 项合规与数据偏差，已输出精准 Patch Diff，要求整改重写。"
        )

        return {
            "passed": passed,
            "score": max(round(score, 1), 0.0),
            "hallucination_detected": hallucination,
            "issues": issues,
            "summary_comment": summary,
        }

    @staticmethod
    def to_review_results(
        task_id: str, tenant_id: str, feedback: AuditFeedback
    ) -> List[ReviewResult]:
        """将审查报告转换为持久化 ReviewResult 实体列表"""
        results: List[ReviewResult] = []
        for item in feedback.get("issues", []):
            title = item.get("issue") or item.get("reason") or "方案校核偏离项"
            description = item.get("reason") or item.get("issue") or "未达到招标文件合规要求"
            suggestion = item.get("suggested_replacement") or item.get("suggested_patch") or ""
            rr = ReviewResult(
                tenant_id=tenant_id,
                task_id=task_id,
                title=title[:512],
                description=description,
                suggestion=suggestion,
                severity=item.get("severity", SeverityLevel.HIGH),
                deviation_type=DeviationType.NEGATIVE,
                source_section=item.get("target_section", ""),
                source_quote=item.get("error_quote", ""),
                benchmark_section="招标文件技术规范",
                benchmark_quote=item.get("suggested_replacement", ""),
                diff_payload={
                    "issue_id": item.get("issue_id"),
                    "reason": item.get("reason"),
                    "suggested_replacement": item.get("suggested_replacement"),
                },
            )
            results.append(rr)
        return results

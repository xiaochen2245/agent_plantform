"""功能① 规则引擎（#30）：审查 docx vs 模板基准 → 结构化问题清单。

确定性规则（不做 LLM 猜测，错别字通道见 #31）：
- R1 font：run 显式字体（ascii/eastAsia）≠ 所属样式基准 → warn
- R2 size：run 显式字号 ≠ 基准 → warn
- R3 para：段落显式对齐 ≠ 基准 → warn
- R4 numbering：numId 不在 numbering.xml（编号引用断裂）→ error；
  自动编号段落文本又以「1./1、」等开头（双重编号）→ warn
"""
import re

from app.review.ooxml import Docx

# 双重编号：段落已挂自动编号，正文又手写了序号
_MANUAL_NUM = re.compile(r"^\s*\d+[.、)．]|^\s*[（(]\d+[)）]")


def _issue(type_: str, severity: str, para, expected, actual, message: str) -> dict:
    return {
        "type": type_,
        "severity": severity,
        "paragraph": para.idx,
        "text": para.text[:24],
        "expected": expected,
        "actual": actual,
        "message": message,
    }


def check(doc: Docx, template: Docx) -> dict:
    """doc 与模板同名样式基准逐项对照；模板无该样式则跳过该样式检查。"""
    baseline = template.style_baseline()
    valid_num_ids = doc.num_ids()
    issues: list[dict] = []

    for para in doc.paragraphs():
        base = baseline.get(para.style) if para.style else baseline.get(None)
        if base is None:  # 模板未定义该样式：仅查编号断裂，样式项跳过
            base = {"ascii": None, "eastAsia": None, "sz": None, "jc": None}

        if para.num_id is not None and para.num_id not in valid_num_ids:
            issues.append(_issue(
                "numbering", "error", para, f"numId {para.num_id} 应在 numbering.xml 定义",
                "引用断裂", f"编号 numId={para.num_id} 未在 numbering.xml 定义"))

        if para.jc is not None and base["jc"] is not None and para.jc != base["jc"]:
            issues.append(_issue(
                "alignment", "warn", para, base["jc"], para.jc,
                f"对齐与模板基准不符（样式 {para.style or '默认'}）"))

        if para.num_id is not None and para.text and _MANUAL_NUM.match(para.text):
            issues.append(_issue(
                "numbering", "warn", para, "自动编号", "手写序号",
                "段落已挂自动编号又手写序号（双重编号）"))

        for run in para.runs:
            for key, label in (("eastAsia", "中文字体"), ("ascii", "西文字体")):
                if key in run.fonts and base[key] and run.fonts[key] != base[key]:
                    issues.append(_issue(
                        "font", "warn", para, base[key], run.fonts[key],
                        f"{label}与模板基准不符（样式 {para.style or '默认'}）"))
            if run.sz is not None and base["sz"] and run.sz != base["sz"]:
                issues.append(_issue(
                    "size", "warn", para, f"{base['sz']/2:g}pt", f"{run.sz/2:g}pt",
                    f"字号与模板基准不符（样式 {para.style or '默认'}）"))

    return {
        "summary": {
            "total_issues": len(issues),
            "by_type": {t: sum(1 for i in issues if i["type"] == t)
                        for t in {i["type"] for i in issues}},
        },
        "issues": issues,
    }

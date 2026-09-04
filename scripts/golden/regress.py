#!/usr/bin/env python3
"""金标集周回归（#35）：评分表抽取的评分项级召回监控。

每个用例 = cases/<名称>/ 下两份文件：
  source.txt     评分表文本块（可从 RAGFlow chunks 导出拼接）
  expected.json  人工标注：{"total": 100, "items": [{"seq","item","score","criteria","category"}, ...]}

运行（需 backend venv 与 .env 里的 SiliconFlow key）：
  cd backend && .venv/bin/python ../scripts/golden/regress.py
  可选：--threshold 0.95（监控线，默认）、--dry-run（不调 LLM，只校验标注文件
  格式与匹配逻辑，用 expected 充当抽取结果 → 召回应为 100%）。

口径：召回 = 命中标注项 / 标注项总数；命中 = 序号+名称归一化相等，或
名称互为包含且分值一致（±0.01）。低于监控线 exit 1（周报用，非交付门槛）。
"""
import argparse
import json
import sys
from pathlib import Path

GOLDEN_DIR = Path(__file__).resolve().parent / "cases"


def _norm(s: str) -> str:
    return "".join(ch for ch in str(s or "").strip() if not ch.isspace())


def match_items(expected: list[dict], extracted: list[dict]) -> list[dict]:
    """返回命中的标注项列表（expected 侧视角）。"""
    hits: list[dict] = []
    ext = [{**e, "_n": _norm(e.get("item"))} for e in extracted]
    for want in expected:
        w_seq, w_item, w_score = _norm(want.get("seq")), _norm(want.get("item")), want.get("score")
        hit = False
        for e in ext:
            if _norm(e.get("seq")) == w_seq and _norm(e.get("item")) == w_item:
                hit = True
                break
            if w_item and (w_item in e["_n"] or e["_n"] in w_item):
                try:
                    if w_score is not None and abs(float(e.get("score")) - float(w_score)) <= 0.01:
                        hit = True
                        break
                except (TypeError, ValueError):
                    continue
        if hit:
            hits.append(want)
    return hits


def load_case(case_dir: Path) -> tuple[list[dict], dict]:
    expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
    items = expected.get("items") or []
    if not items:
        raise ValueError(f"{case_dir.name}: expected.json 缺 items")
    return items, expected


async def extract_online(source_text: str) -> list[dict] | None:
    """真跑 #33 抽取器（backend venv + SiliconFlow key）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
    from app.compare.extract import ScoringTableExtractor

    table = await ScoringTableExtractor().extract([{"content": source_text}])
    return [i.model_dump() for i in table.items] if table else None


def main() -> int:
    ap = argparse.ArgumentParser(description="金标集周回归（评分项级召回）")
    ap.add_argument("--threshold", type=float, default=0.95)
    ap.add_argument("--dry-run", action="store_true", help="不调 LLM，只验格式与匹配逻辑")
    args = ap.parse_args()

    cases = sorted(p for p in GOLDEN_DIR.iterdir() if p.is_dir()) if GOLDEN_DIR.exists() else []
    if not cases:
        print(f"无金标用例：{GOLDEN_DIR}（标注就绪后放入，格式见 scripts/golden/README.md）")
        return 0

    import asyncio

    failed: list[str] = []
    recalls: list[float] = []
    for case in cases:
        try:
            items, _ = load_case(case)
        except (ValueError, json.JSONDecodeError) as e:
            print(f"[{case.name}] 标注文件不合规: {e}")
            failed.append(case.name)
            continue
        if args.dry_run:
            extracted = items  # 自充当抽取结果
        else:
            extracted = asyncio.run(extract_online((case / "source.txt").read_text(encoding="utf-8")))
        if extracted is None:
            print(f"[{case.name}] 抽取失败（重试用尽）")
            failed.append(case.name)
            continue
        hits = match_items(items, extracted)
        recall = len(hits) / len(items) if items else 0.0
        recalls.append(recall)
        miss = [i for i in items if i not in hits]
        print(f"[{case.name}] 召回 {recall:.0%}（{len(hits)}/{len(items)}）"
              + (f" 漏检: {[i.get('item') for i in miss]}" if miss else ""))
        if recall < args.threshold:
            failed.append(case.name)

    overall = sum(recalls) / len(recalls) if recalls else 0.0
    print(f"\n总体召回 {overall:.1%}，监控线 {args.threshold:.0%}，"
          f"{'通过 ✓' if not failed else '未过 ✗: ' + ', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

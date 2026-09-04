"""#35 金标回归脚本：match_items 纯逻辑（离线，不调 LLM）。"""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "golden_regress", Path(__file__).resolve().parents[2] / "scripts/golden/regress.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
match_items = _mod.match_items


def test_exact_seq_item_match():
    exp = [{"seq": "1", "item": "投标报价", "score": 30}]
    ext = [{"seq": "1", "item": "投标报价", "score": 30, "criteria": "x"}]
    assert len(match_items(exp, ext)) == 1


def test_name_containment_with_score_tolerance():
    exp = [{"seq": "2.1", "item": "技术方案", "score": 40}]
    ext = [{"seq": "A", "item": "技术方案的完整性与先进性", "score": 40.0}]
    assert len(match_items(exp, ext)) == 1  # 名称包含 + 分值一致 → 命中


def test_containment_with_wrong_score_misses():
    exp = [{"seq": "2.1", "item": "技术方案", "score": 40}]
    ext = [{"seq": "A", "item": "技术方案完整性", "score": 20}]
    assert match_items(exp, ext) == []


def test_whitespace_normalized():
    exp = [{"seq": " 1 ", "item": "投标 报价", "score": 30}]
    ext = [{"seq": "1", "item": "投标报价", "score": 30}]
    assert len(match_items(exp, ext)) == 1


def test_empty_extraction_hits_nothing():
    assert match_items([{"seq": "1", "item": "x", "score": 1}], []) == []

"""功能② 评分表结构化拆解（#33）：schema 校验 / 抽取重试 / API。"""
import asyncio
import json

import httpx
import pytest
from httpx import AsyncClient

from app.compare.extract import ScoringTableExtractor
from app.compare.schema import ScoringItem, ScoringTable
from tests.conftest import login

GOOD = {
    "total": 100,
    "items": [
        {"seq": "1", "item": "投标报价", "score": 30, "criteria": "以基准价计算得分", "category": "价格"},
        {"seq": "2.1", "item": "技术方案", "score": 40, "criteria": "方案完整性≥P=（评标价-基准价）/基准价×100", "category": "技术"},
        {"seq": "2.2", "item": "项目经理业绩", "score": 30, "criteria": "近5年同类业绩每项10分", "category": "技术"},
    ],
}


def _llm(responses: list[str]):
    """按序回放的假 SiliconFlow：第 i 次调用吐 responses[i]。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        content = responses[min(len(calls) - 1, len(responses) - 1)]
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return httpx.MockTransport(handler), calls


def test_scoring_table_schema_and_sum_warning():
    t = ScoringTable(total=100, items=[
        ScoringItem(item="报价", score=30, criteria="", category="价格"),
        ScoringItem(item="技术", score=40, criteria="公式照抄"),
        ScoringItem(item="业绩", score=35, criteria=""),  # 30+40+35=105 ≠ 100
    ])
    t.check_sum()
    assert t.warnings and "105" in t.warnings[0] and "100" in t.warnings[0]

    # 非法 category 归空、非法 score 拒收
    assert ScoringItem(item="x", score=1, category="别的").category == ""
    with pytest.raises(Exception):
        ScoringItem(item="x", score=-1)


def test_extract_success_with_fenced_json():
    fenced = f"```json\n{json.dumps(GOOD, ensure_ascii=False)}\n```"
    transport, calls = _llm([fenced])
    table = asyncio.run(ScoringTableExtractor(transport=transport).extract(
        [{"content": "评分办法…"}] * 3))
    assert table is not None
    assert len(table.items) == 3 and table.total == 100
    assert not table.warnings
    assert len(calls) == 1


def test_extract_retries_on_schema_mismatch_then_success():
    bad = '{"total": 5, "items": [{"item": "", "score": "不是数字"}]}'
    transport, calls = _llm([bad, json.dumps(GOOD, ensure_ascii=False)])
    table = asyncio.run(ScoringTableExtractor(transport=transport).extract(
        [{"content": "评分办法"}]))
    assert table is not None and table.items[0].item == "投标报价"
    assert len(calls) == 2, "首调失败后必须重试一次"


def test_extract_gives_up_after_retries():
    transport, calls = _llm(["垃圾输出"])
    out = asyncio.run(ScoringTableExtractor(transport=transport).extract(
        [{"content": "评分办法"}]))
    assert out is None and len(calls) == 2


def test_extract_empty_chunks_returns_none():
    out = asyncio.run(ScoringTableExtractor().extract([]))
    assert out is None


async def test_scoring_table_api(client: AsyncClient, monkeypatch):
    from app.compare.router import ScoringTableExtractor as RExtractor

    class FakeExtractor:
        def __init__(self): self.calls = 0
        async def extract(self, chunks):
            self.calls += 1
            return ScoringTable(total=100, items=[ScoringItem(item="报价", score=100, category="价格")])

    fake_ext = FakeExtractor()
    monkeypatch.setattr("app.compare.router.ScoringTableExtractor", lambda: fake_ext)

    from app.ragflow.client import RagflowClient
    from tests.test_ragflow import fake_ragflow

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chunks") and request.method == "GET":
            return httpx.Response(200, json={"code": 0, "data": {"chunks": [{"content": "评分表"}]}})
        return httpx.Response(200, json={"code": 0, "data": []})

    fake = RagflowClient(base_url="http://fake", api_key="k", transport=httpx.MockTransport(handler))
    from app.ragflow.deps import get_ragflow
    from app.main import app
    app.dependency_overrides[get_ragflow] = lambda: fake

    await login(client)
    r = await client.post("/api/compare/scoring-table",
                          json={"dataset_id": "ds-1", "document_id": "doc-1"})
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["item"] == "报价" and r.json()["total"] == 100

    # 无 chunks → 409
    empty = fake_ragflow()

    def no_chunks(request):
        return httpx.Response(200, json={"code": 0, "data": {"chunks": []}})

    empty._client._transport = httpx.MockTransport(no_chunks)
    app.dependency_overrides[get_ragflow] = lambda: empty
    r = await client.post("/api/compare/scoring-table",
                          json={"dataset_id": "ds-1", "document_id": "doc-1"})
    assert r.status_code == 409

    # 校验失败（重试用尽）→ 502
    class NoneExtractor:
        async def extract(self, chunks):
            return None

    monkeypatch.setattr("app.compare.router.ScoringTableExtractor", lambda: NoneExtractor())
    app.dependency_overrides[get_ragflow] = lambda: fake
    r = await client.post("/api/compare/scoring-table",
                          json={"dataset_id": "ds-1", "document_id": "doc-1"})
    assert r.status_code == 502

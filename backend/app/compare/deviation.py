"""比对流水线（#34）：评分表 vs 投标响应 → 缺失项/偏离建议（人审 API）。

P1 立场：一切输出是「建议」不是结论（正/负偏离判定归人审）。
LLM 逐项裁决 + 服务端 join 校验：LLM 漏判的评分项服务端补 missing+warning，
不静默丢项。
"""
import logging

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.compare.schema import ScoringTable
from app.core.config import settings
from app.core.llm import chat_json

_logger = logging.getLogger("app.compare.deviation")

MAX_RESPONSE_CHARS = 12000

PROMPT = """你是评标助理。下面是招标评分表（JSON）和投标响应文本。对每个评分项判断投标文件的响应情况，只输出 JSON 对象：
{{"verdicts": [{{"seq": "评分项序号原样回填", "item": "评分项名称原样回填", "status": "responded|partial|missing", "deviation": "none|positive|negative|unknown", "evidence": "响应文本中的依据片段（没有留空）", "suggestion": "给人审的一条建议（如：需补充业绩证明/承诺函缺失）"}}]}}
要求：
- status：responded=明确响应 / partial=部分响应 / missing=未响应
- deviation：positive=优于要求 / negative=低于要求 / none=符合 / unknown=文本无法判断（默认 unknown，不臆断）
- 每个评分项必须有一条 verdict，序号名称原样回填
- 判断只依据响应文本，不臆造

评分表：
{table}

投标响应文本：
{response}"""


class ItemVerdict(BaseModel):
    seq: str = Field(default="", max_length=32)
    item: str = Field(min_length=1, max_length=256)
    status: str = Field(pattern="^(responded|partial|missing)$")
    deviation: str = Field(default="unknown", pattern="^(none|positive|negative|unknown)$")
    evidence: str = Field(default="", max_length=1024)
    suggestion: str = Field(default="", max_length=512)


class CompareResult(BaseModel):
    verdicts: list[ItemVerdict] = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)


class Comparator:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def compare(self, table: ScoringTable, response_chunks: list[dict]) -> CompareResult | None:
        text = "\n".join(
            (c.get("content") or c.get("content_with_weight") or "") for c in response_chunks
        ).strip()[:MAX_RESPONSE_CHARS]
        if not text:
            return None
        data = await chat_json(
            PROMPT.format(table=table.model_dump_json(), response=text),
            model=settings.SILICONFLOW_CHAT_MODEL,
            max_tokens=4096,
            transport=self._transport,
        )
        if data is None:
            return None
        warnings: list[str] = list(table.warnings)  # 评分表自身的总分告警带入
        try:
            got = [ItemVerdict(**v) for v in data.get("verdicts", [])]
        except (ValidationError, TypeError):
            _logger.warning("verdict schema mismatch: %.200s", data)
            return None
        # 服务端 join：LLM 漏判的项补 missing，绝不静默丢项
        by_key = {(v.seq, v.item): v for v in got}
        verdicts: list[ItemVerdict] = []
        for it in table.items:
            v = by_key.get((it.seq, it.item))
            if v is None:
                verdicts.append(ItemVerdict(
                    seq=it.seq, item=it.item, status="missing", deviation="unknown",
                    suggestion="（服务端补判）LLM 未覆盖该评分项，请人审核对",
                ))
                warnings.append(f"LLM 漏判评分项 {it.seq} {it.item}，已按未响应补判")
            else:
                verdicts.append(v)
        if not verdicts:
            return None
        return CompareResult(verdicts=verdicts, warnings=warnings)

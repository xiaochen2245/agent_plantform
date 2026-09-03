"""评分表抽取器（#33）：RAGFlow chunks → LLM → ScoringTable（校验+重试）。

失败语义：重试用尽仍不合 schema → None（调用方 502）；宁可失败不写猜测——
错误结构化会让 #34 比对逐项失真。
"""
import logging

import httpx
from pydantic import ValidationError

from app.compare.schema import ScoringTable
from app.core.config import settings
from app.core.llm import chat_json

_logger = logging.getLogger("app.compare.extract")

MAX_RETRIES = 2          # 首调 + 重试
MAX_INPUT_CHARS = 12000  # 评分表 8 表格块量级；更长的截断（金标集就绪后再评估分块策略）

PROMPT = """你是招标文件解析员。下面是招标文件中评分表的文本块（可能含多个表格片段），抽取所有评分项，只输出 JSON 对象：
{{"total": 评分办法声明的总分（数字，找不到用 null）, "items": [{{"seq": "序号", "item": "评分项名称", "score": 分值数字, "criteria": "评审标准原文（含公式照抄）", "category": "价格/技术/商务，判断不出用空字符串"}}]}}
要求：
- 分值取数字（"5分"→5，"权重20%"按所属分组合计口径给数字）
- 不臆造：文本块中没有的项不要编
- 找不到评分项时输出 {{"total": null, "items": []}}

评分表文本：
{content}"""


class ScoringTableExtractor:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def extract(self, chunks: list[dict]) -> ScoringTable | None:
        text = "\n".join(
            (c.get("content") or c.get("content_with_weight") or "") for c in chunks
        ).strip()[:MAX_INPUT_CHARS]
        if not text:
            return None
        for attempt in range(MAX_RETRIES):
            data = await chat_json(
                PROMPT.format(content=text),
                model=settings.SILICONFLOW_CHAT_MODEL,
                max_tokens=4096,
                transport=self._transport,
            )
            if data is None:
                continue  # HTTP/JSON 层失败也重试一次
            try:
                table = ScoringTable(**{
                    "total": data.get("total"),
                    "items": data.get("items") or [],
                })
            except ValidationError:
                _logger.warning("scoring schema mismatch (attempt %d): %.200s", attempt + 1, data)
                continue
            table.check_sum()
            return table
        return None

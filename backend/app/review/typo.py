"""功能① 错别字 LLM 辅助通道（#31）：候选+置信度，不做自动改。

调用模式与 app/ragflow/tagging.py 同款（SiliconFlow OpenAI 兼容 + JSON 输出
+ pydantic 校验，失败宁缺勿错）。与规则引擎（#30）互补：规则查格式确定性
问题，这里查语义性错别字，人审决定改不改。
"""
import json
import logging

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.core.config import settings

_logger = logging.getLogger("app.ragflow.typo")

MAX_INPUT_CHARS = 8000  # 与 tagging 同档；超长截断（长文档一致性全量在 P2）


class TypoCandidate(BaseModel):
    orig: str = Field(max_length=64)        # 原词
    suggestion: str = Field(max_length=64)  # 建议改法
    confidence: float = Field(ge=0.0, le=1.0)
    paragraph: int = Field(ge=0)            # 段落序号（与 #30 报告定位同口径）
    context: str = Field(default="", max_length=128)  # 前后文片段


PROMPT = """你是校对员。下面是带段落编号 [P<n>] 的文档内容，找出其中的错别字（别字/多字/漏字/同音异义）。
只输出 JSON 对象，不要任何其他文字，格式：
{{"typos": [{{"orig": "原词", "suggestion": "正确写法", "confidence": 0.0到1.0, "paragraph": 段落序号n, "context": "该词所在的前后文片段"}}]}}
要求：
- 只列有把握的错别字，没有则输出 {{"typos": []}}
- 不改标点/格式/专业术语拼写
- confidence 按把握给，人审按置信度过滤

文档内容：
{content}"""


class TypoChecker:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def _chat(self, content: str) -> str:
        body = {
            "model": settings.SILICONFLOW_TYPO_MODEL,
            "messages": [
                {"role": "system", "content": "你只输出 JSON。"},
                {"role": "user", "content": PROMPT.format(content=content)},
            ],
            "temperature": 0.1,
            "max_tokens": 2048,
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(
            base_url=settings.SILICONFLOW_BASE_URL.rstrip("/"),
            timeout=httpx.Timeout(120.0, connect=10.0),
            transport=self._transport,
            trust_env=False,
        ) as c:
            r = await c.post(
                "/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {settings.SILICONFLOW_API_KEY}"},
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]

    async def check(self, paragraphs: list[tuple[int, str]]) -> list[TypoCandidate] | None:
        """[(段落序号, 文本)] → 候选清单；LLM/解析失败返回 None（调用方 502 或重试）。"""
        text = "\n".join(f"[P{idx}] {t}" for idx, t in paragraphs if t.strip())[:MAX_INPUT_CHARS]
        if not text:
            return []
        try:
            raw = (await self._chat(text)).strip()
        except httpx.HTTPError as e:
            _logger.warning("typo llm error: %s", e)
            return None
        if raw.startswith("```"):
            raw = raw.strip("`").removeprefix("json").strip()
        try:
            data = json.loads(raw)
            return [TypoCandidate(**t) for t in data.get("typos", [])]
        except (json.JSONDecodeError, ValidationError, TypeError):
            _logger.warning("typo non-json/schema mismatch: %.200s", raw)
            return None

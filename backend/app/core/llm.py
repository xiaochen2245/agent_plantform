"""SiliconFlow JSON-LLM 共享底座：chat → 只输出 JSON → 解析为 dict。

第三处同款调用（tagging / review.typo / compare.extract）时抽取；
pydantic 业务校验留在各调用方（schema 各异，此处只管管道）。
"""
import json
import logging

import httpx

from app.core.config import settings

_logger = logging.getLogger("app.core.llm")


async def chat_json(
    user_prompt: str,
    *,
    model: str,
    max_tokens: int = 512,
    temperature: float = 0.1,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict | None:
    """OpenAI 兼容 JSON-mode 调用；HTTP 错误/非 JSON/非对象一律 None（宁缺勿错）。"""
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你只输出 JSON。"},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.SILICONFLOW_BASE_URL.rstrip("/"),
            timeout=httpx.Timeout(120.0, connect=10.0),
            transport=transport,
            trust_env=False,
        ) as c:
            r = await c.post(
                "/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {settings.SILICONFLOW_API_KEY}"},
            )
            r.raise_for_status()
            raw = r.json()["choices"][0]["message"]["content"].strip()
    except httpx.HTTPError as e:
        _logger.warning("llm call error (%s): %s", model, e)
        return None
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        _logger.warning("llm non-json (%s): %.200s", model, raw)
        return None
    return data if isinstance(data, dict) else None

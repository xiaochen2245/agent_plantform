"""功能④打标管道：入库文档 → LLM 抽取业务标签 → RAGFlow document metadata。

- LLM：SiliconFlow chat（OpenAI 兼容），网关直连，key 不落 RAGFlow（共享底座
  app/core/llm.chat_json）
- 输出 schema 用 pydantic 校验；LLM 返回不合规则打标失败（宁缺勿错——
  错误标签会让元数据过滤检索静默失真）
- meta_fields 落 RAGFlow 后，retrieval 的 metadata_condition 直接可过滤
"""
import logging

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.core.config import settings
from app.core.llm import chat_json

_logger = logging.getLogger("app.ragflow.tagging")


class ExtractedLabels(BaseModel):
    """功能④核心标签集：跨项目经验关联的过滤维度。"""

    project: str = Field(default="", max_length=128)      # 项目名（如 XX市政管网改造）
    discipline: str = Field(default="", max_length=64)    # 专业（给排水/电气/结构…）
    doc_type: str = Field(default="", max_length=64)      # 文档类型（设计审查单/经验反馈/里程碑…）
    date: str = Field(default="", max_length=32)          # 文档所属时间 YYYY 或 YYYY-MM
    keywords: list[str] = Field(default_factory=list, max_length=10)

PROMPT = """你是工程文档编目员。从下面的文档内容中抽取结构化标签，只输出 JSON 对象，不要任何其他文字。
字段：
- project: 项目名称，找不到用空字符串
- discipline: 专业（如：给排水/电气/暖通/结构/市政/信息化），找不到用空字符串
- doc_type: 文档类型（设计审查单/经验反馈表/项目里程碑/招标文件/技术方案/其他）
- date: 文档所属年月，格式 YYYY 或 YYYY-MM，找不到用空字符串
- keywords: 3-8 个关键问题词（如：管道埋深/冻土线/阀门井间距）

文档内容：
{content}"""


class Tagger:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def extract(self, chunks: list[dict]) -> ExtractedLabels | None:
        """chunks → 标签；抽取/解析失败返回 None（调用方决定重试或人补）。"""
        text = "\n".join(
            (c.get("content") or c.get("content_with_weight") or "")[:600]
            for c in chunks[:12]
        ).strip()
        if not text:
            return None
        data = await chat_json(
            PROMPT.format(content=text[:8000]),
            model=settings.SILICONFLOW_CHAT_MODEL,
            transport=self._transport,
        )
        if data is None:
            return None
        try:
            return ExtractedLabels(**data)
        except ValidationError:
            _logger.warning("tagging schema mismatch: %.200s", data)
            return None

    @staticmethod
    def to_meta_fields(labels: ExtractedLabels) -> dict:
        return {
            "project": labels.project,
            "discipline": labels.discipline,
            "doc_type": labels.doc_type,
            "date": labels.date,
            "keywords": ",".join(labels.keywords),
        }

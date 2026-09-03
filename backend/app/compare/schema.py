"""评分表结构化 schema（#33 / 功能②）。

真实评分表形态（spike 9 份招标 PDF 结论）：分项含 序号/评分项/分值/评审标准，
常按 价格/技术/商务 分组；上海文件含公式（评审标准以文本承载）。
总分与分项和可能不一致（LLM 抽取误差或原文如此）→ warning 不拒收，
由人审核对（P1 一律不做自动结论）。
"""
from pydantic import BaseModel, Field, field_validator


class ScoringTableQuery(BaseModel):
    """#33 请求体：从已解析文档抽取评分表。"""

    dataset_id: str = Field(min_length=1, max_length=64)
    document_id: str = Field(min_length=1, max_length=64)


class CompareQuery(BaseModel):
    """#34 请求体：招标评分表文档 + 投标响应文档（均已解析）。"""

    scoring: ScoringTableQuery
    response: ScoringTableQuery


class ScoringItem(BaseModel):
    seq: str = Field(default="", max_length=32)        # 序号（1 / 1.1 / 一）
    item: str = Field(min_length=1, max_length=256)    # 评分项名称
    score: float = Field(ge=0, le=1000)                # 分值
    criteria: str = Field(default="", max_length=2048) # 评审标准（可含公式文本）
    category: str = Field(default="", max_length=64)   # 价格/技术/商务/其他

    @field_validator("category")
    @classmethod
    def _norm_category(cls, v: str) -> str:
        return v.strip() if v.strip() in ("价格", "技术", "商务") else ""


class ScoringTable(BaseModel):
    total: float | None = Field(default=None, ge=0, le=10000)  # 声明总分（原文口径）
    items: list[ScoringItem] = Field(min_length=1, max_length=200)
    warnings: list[str] = Field(default_factory=list)

    def check_sum(self) -> None:
        """分项和 vs 声明总分不一致 → warning（不拒收，人审判断）。"""
        if self.total is None or not self.items:
            return
        s = sum(i.score for i in self.items)
        if abs(s - self.total) > 0.01:
            self.warnings.append(f"分项和 {s:g} ≠ 声明总分 {self.total:g}（请人审核对）")

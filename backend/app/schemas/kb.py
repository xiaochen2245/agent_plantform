"""知识库 DTO（契约 v7）：请求体仅此两个；响应一律透传 Dify 形状。"""
from pydantic import BaseModel, Field


class TextDocCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1)
    indexing_technique: str = Field(pattern="^(high_quality|economy)$")


class RetrieveQuery(BaseModel):
    query: str = Field(min_length=1, max_length=2000)

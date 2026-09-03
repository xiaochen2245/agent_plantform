"""RAG 网关路由请求体（响应直接透传 dict——上游形状尚未冻结，不提前建模）。"""
from pydantic import BaseModel, Field


class RagDatasetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=512)


class RagRetrievalQuery(BaseModel):
    question: str = Field(min_length=1, max_length=2048)
    dataset_ids: list[str] = Field(min_length=1, max_length=16)
    top_k: int = Field(default=5, ge=1, le=50)

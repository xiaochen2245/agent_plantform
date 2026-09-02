"""知识库 DTO（契约 v7/v9）：请求体仅此四个；响应一律透传 Dify 形状。"""
from pydantic import BaseModel, Field


class TextDocCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1)
    indexing_technique: str = Field(pattern="^(high_quality|economy)$")


class RetrieveQuery(BaseModel):
    query: str = Field(min_length=1, max_length=2000)


class DatasetCreate(BaseModel):
    """契约 v9：建库。"""

    name: str = Field(min_length=1, max_length=100)
    indexing_technique: str = Field(default="high_quality", pattern="^(high_quality|economy)$")


class GrantCreate(BaseModel):
    """契约 v9：授权主体（user/dept/role 三态，同 app 授权模型）。"""

    principal_type: str = Field(pattern="^(user|dept|role)$")
    principal_id: int = Field(ge=1)

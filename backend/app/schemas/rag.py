"""RAG 网关请求体（响应直接透传 dict——上游形状尚未冻结，不提前建模）。"""
from pydantic import BaseModel, Field


class RagDatasetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=512)


class MetaCond(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    comparison_operator: str = Field(default="is", max_length=16)
    value: str = Field(default="", max_length=256)


class MetadataCondition(BaseModel):
    logic: str = Field(default="and", pattern="^(and|or)$")
    conditions: list[MetaCond] = Field(min_length=1, max_length=8)


class RagRetrievalQuery(BaseModel):
    question: str = Field(min_length=1, max_length=2048)
    dataset_ids: list[str] = Field(min_length=1, max_length=16)
    top_k: int = Field(default=5, ge=1, le=50)
    # 租内过滤（部门/专业/项目维度），透传 RAGFlow metadata_condition；
    # 租户隔离在账号层，此处仅收窄范围不放宽
    metadata_condition: MetadataCondition | None = None


class RagBindingCreate(BaseModel):
    department_id: int
    email_prefix: str = Field(default="", max_length=64)  # 缺省 dept-<id>@ragflow.local


class RagSessionCreate(BaseModel):
    """#38 会话创建：app_id 关联门户应用（1=知识库 2=审查 3=比对），缺省知识库。"""
    app_id: int = 1
    title: str = Field(default="", max_length=200)


class RagSessionMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=16384)


class RagSessionSync(BaseModel):
    """全量同步：客户端上报当前会话完整轮次（幂等重写）。"""
    messages: list[RagSessionMessage] = Field(max_length=200)
    title: str | None = Field(default=None, max_length=200)

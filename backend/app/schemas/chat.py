"""Apps / Conversations / Chat DTO（形状严格对齐 docs/api-contract.md）。"""
from pydantic import BaseModel, Field


class AppOut(BaseModel):
    id: int
    name: str
    description: str
    mode: str


class AppsResponse(BaseModel):
    apps: list[AppOut]


class ChatSendRequest(BaseModel):
    app_id: int
    query: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = ""  # 内部 UUID；空串=新建


class ConversationOut(BaseModel):
    id: str
    title: str
    message_count: int
    updated_at: str


class ConversationsResponse(BaseModel):
    items: list[ConversationOut]

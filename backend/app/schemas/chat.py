"""Apps / Conversations / Chat DTO（形状严格对齐 docs/api-contract.md）。"""
from pydantic import BaseModel, Field


class AppOut(BaseModel):
    id: int
    name: str
    description: str
    mode: str
    inputs_schema: list[dict] | None = None  # 契约 v3：仅 workflow 应用非空


class AppsResponse(BaseModel):
    apps: list[AppOut]


class FileUploadedOut(BaseModel):
    file_id: str
    name: str
    size: int
    mime: str


class ChatSendRequest(BaseModel):
    app_id: int
    # 契约 v3：query 对 workflow 应用非必需（inputs 承载输入）；对 chat/agent 必填由 handler 判定
    query: str | None = Field(default=None, max_length=8000)
    inputs: dict[str, str] | None = None  # 契约 v3：workflow 应用变量透传
    conversation_id: str | None = ""  # 内部 UUID；空串=新建
    files: list[str] | None = None  # 契约 v4：附件 file_id 列表（chat/agent 模式有效）


class ConversationOut(BaseModel):
    id: str
    title: str
    message_count: int
    updated_at: str


class ConversationsResponse(BaseModel):
    items: list[ConversationOut]


class MessageOut(BaseModel):
    id: int
    role: str  # 'user' | 'assistant'
    content: str
    created_at: str
    files: list | None = None  # 契约 v4：[{file_id,name,size,mime,dify_file_id?}]


class MessagesResponse(BaseModel):
    messages: list[MessageOut]

"""Conversation 模型：内部 UUID 主键 + Dify conversation_id 关联。

- UUID 主键避免对外泄露平台规模（设计 §5.7 / §6 决策 1）
- deleted_at 软删保留审计痕迹
- token_usage 承载 message_end 的 usage 快照（设计 §13.4）
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String, func
from sqlalchemy import Uuid as SageUuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(SageUuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("apps.id"), nullable=False, index=True)
    dify_conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    title: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    message_count: Mapped[int] = mapped_column(default=0, nullable=False)
    token_usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

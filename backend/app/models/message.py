"""Message 模型：对话镜像（user / assistant 双写，设计 §5.2 finally 落库）。"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, func
from sqlalchemy import Uuid as SageUuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# 与 user.py / app.py 同一 idiom：SQLite 自增需要 INTEGER 变体
from sqlalchemy import BigInteger, Integer

PK = BigInteger().with_variant(Integer, "sqlite")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        SageUuid, ForeignKey("conversations.id"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # 'user' | 'assistant'
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    dify_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    files: Mapped[list | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

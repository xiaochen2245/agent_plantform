"""RagAuditLog 模型：RAGFlow 写操作审计（#28）。

与 KbAuditLog 同形状（契约 v9 先例）：user_id 不加外键——审计行必须在
用户被删后仍可追溯；detail 为 JSON 字符串。两表并存：kb_audit_logs 是
Dify kb 遗留生命周期，RAG 侧独立演进。
"""
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.app import PK


class RagAuditLog(Base):
    __tablename__ = "rag_audit_logs"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(PK, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    # dataset.create / dataset.update / dataset.delete / doc.upload / doc.delete /
    # doc.tag / binding.create
    dataset_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

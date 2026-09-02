"""KbAuditLog 模型：知识库写操作审计（契约 v9）。

只记写路径（建库/删库/文档增删/授权变更）；user_id 不加外键——审计行必须
在用户被删后仍可追溯。detail 为 JSON 字符串（文件名/文档名等上下文）。
"""
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.app import PK


class KbAuditLog(Base):
    __tablename__ = "kb_audit_logs"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(PK, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    dataset_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

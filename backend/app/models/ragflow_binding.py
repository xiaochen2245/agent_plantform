"""RagflowBinding：平台部门 → RAGFlow 影子账号绑定（W2 租户映射）。

- 部门 = P1 租户单元（owner 决策 #2 落定前先用现有部门结构；外部客户级租户将来加层）
- 凭证加密存储（core.vault Fernet）；token 过期可用存储密码重登换发
- 开通链路见 ragflow/onboarding.py（全自动，零 RAGFlow UI 操作）
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

PK = BigInteger().with_variant(Integer, "sqlite")


class RagflowBinding(Base):
    __tablename__ = "ragflow_bindings"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    department_id: Mapped[int] = mapped_column(PK, unique=True, nullable=False)
    ragflow_email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    ragflow_password_enc: Mapped[str] = mapped_column(String(512), nullable=False)
    ragflow_api_token_enc: Mapped[str] = mapped_column(String(512), nullable=False)
    default_dataset_id: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|disabled
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

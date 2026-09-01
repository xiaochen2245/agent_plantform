"""Role 模型 + user_roles 关联表（设计 §4.2）。

users.roles 旧 JSON 列在启动迁移（db/init.py）后仅作历史快照，
权限一律以 user_roles 为准。
"""
from sqlalchemy import BigInteger, ForeignKey, Integer, String, Table, Column
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

PK = BigInteger().with_variant(Integer, "sqlite")

user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", PK, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", PK, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)

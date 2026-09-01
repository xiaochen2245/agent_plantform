"""Department 模型（设计 §4.2：多级部门 + 物化路径）。

本切片仅建表供 app_authorizations 的 dept 主体引用；
部门/角色 CRUD 端点不在范围内。
"""
from sqlalchemy import BigInteger, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

PK = BigInteger().with_variant(Integer, "sqlite")


class Department(Base):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(PK, nullable=True)
    path: Mapped[str | None] = mapped_column(String(500), nullable=True)  # 物化路径 /1/3/7/

"""AppAuthorization 模型：三态主体授权（设计 §4.3）。

principal_type ∈ {'user','dept','role'}；principal_id 指向对应表主键。
用户可见 = user 直授 ∪ 所属部门 ∪ 拥有角色；解析见 app/authz.py。
本切片仅开放用户级管理端点，dept/role 级授权通过 SQL/后续端点维护。
"""
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.app import PK


class AppAuthorization(Base):
    __tablename__ = "app_authorizations"
    __table_args__ = {"comment": "app_id + (principal_type, principal_id) 复合主键"}

    app_id: Mapped[int] = mapped_column(
        PK, ForeignKey("apps.id", ondelete="CASCADE"), primary_key=True
    )
    principal_type: Mapped[str] = mapped_column(String(10), primary_key=True)
    principal_id: Mapped[int] = mapped_column(PK, primary_key=True)

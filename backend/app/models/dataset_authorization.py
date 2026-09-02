"""DatasetAuthorization 模型：知识库租户隔离（网关映射，契约 v8）。

dataset_id 为 Dify 侧 UUID（无本地外键——Dify 是真相源）；principal 三态同
AppAuthorization。可见 = user 直授 ∪ 所属部门 ∪ 拥有角色；解析复用 app/authz.py。
管理端点本切片仅用户级；dept/role 级通过 SQL/后续端点维护（同 app 授权先例）。
"""
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.app import PK


class DatasetAuthorization(Base):
    __tablename__ = "dataset_authorizations"
    __table_args__ = {"comment": "dataset_id + (principal_type, principal_id) 复合主键"}

    dataset_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    principal_type: Mapped[str] = mapped_column(String(10), primary_key=True)
    principal_id: Mapped[int] = mapped_column(PK, primary_key=True)

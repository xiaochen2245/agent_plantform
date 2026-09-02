"""部门 DTO（契约 v2 §Admin 补齐）。

path 为物化路径 `/1/3/7/`：建/移父时由 service 维护。
PATCH 语义：`parent_id=null` 表示移到顶级；不传则保留（model_dump(exclude_unset=True) 区分）。
"""
from pydantic import BaseModel, Field


class DeptOut(BaseModel):
    id: int
    name: str
    parent_id: int | None
    path: str | None


class DeptsResponse(BaseModel):
    items: list[DeptOut]


class DeptCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    parent_id: int | None = None


class DeptUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    parent_id: int | None = None  # 显式 null = 移到顶级

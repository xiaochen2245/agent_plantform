"""角色 DTO（契约 v2 §Admin 补齐）。

内置保护：USER / PLATFORM_ADMIN 不可删除（service 校验）。
"""
from pydantic import BaseModel, Field


class RoleOut(BaseModel):
    id: int
    code: str
    name: str


class RolesResponse(BaseModel):
    items: list[RoleOut]


class RoleCreate(BaseModel):
    code: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    name: str = Field(min_length=1, max_length=100)


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)

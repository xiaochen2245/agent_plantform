"""Admin DTO（契约 v2 §Admin）。"""
from pydantic import BaseModel, EmailStr, Field


class AdminUserOut(BaseModel):
    id: int
    name: str
    email: str
    dept: str | None
    roles: list[str]
    status: int
    created_at: str


class AdminUsersResponse(BaseModel):
    total: int
    items: list[AdminUserOut]


class AdminUserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    dept_id: int | None = None
    roles: list[str] = Field(default_factory=lambda: ["USER"])


class AdminUserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    dept_id: int | None = None
    roles: list[str] | None = None
    status: int | None = Field(default=None, ge=0, le=1)


class ResetPasswordResponse(BaseModel):
    password: str


class UserAppsResponse(BaseModel):
    app_ids: list[int]


class UserAppsUpdate(BaseModel):
    app_ids: list[int]

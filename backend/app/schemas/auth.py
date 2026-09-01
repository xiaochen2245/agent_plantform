"""认证 DTO（形状严格对齐 docs/api-contract.md）。"""
from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)


class MeResponse(BaseModel):
    id: int
    email: str
    name: str
    roles: list[str]
    dept_id: int | None

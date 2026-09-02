"""角色业务：建/改/删（设计 §4.5 补齐）。

保护策略：
- 内置 USER / PLATFORM_ADMIN 禁止删除（防把全员都锁在外面 / 把唯一管理员删掉）
- 删除前清理 user_roles 和 app_authorizations（FK 无 cascade）

返回约定：
- list_roles  → list[dict]
- create_role → dict | str（'DUPLICATE_CODE'）
- update_role → dict | None
- delete_role → str | None（None=不存在；'BUILTIN' 拒绝）
"""
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_authorization import AppAuthorization
from app.models.role import Role, user_roles

BUILTIN_CODES = {"USER", "PLATFORM_ADMIN"}


def _role_out(r: Role) -> dict:
    return {"id": r.id, "code": r.code, "name": r.name}


async def list_roles(session: AsyncSession) -> list[dict]:
    rows = (await session.execute(select(Role).order_by(Role.id))).scalars().all()
    return [_role_out(r) for r in rows]


async def create_role(
    session: AsyncSession, code: str, name: str
) -> dict | str:
    code = code.upper()
    if await session.scalar(select(Role).where(Role.code == code)) is not None:
        return "DUPLICATE_CODE"
    role = Role(code=code, name=name)
    session.add(role)
    await session.flush()
    return _role_out(role)


async def update_role(
    session: AsyncSession, role_id: int, payload: dict
) -> dict | None:
    role = await session.get(Role, role_id)
    if role is None:
        return None
    if "name" in payload and payload["name"] is not None:
        role.name = payload["name"]
    return _role_out(role)


async def delete_role(session: AsyncSession, role_id: int) -> str | None:
    """不存在 → None；内置角色 → 'BUILTIN'；否则清理 user_roles / app_authorizations 后删行。"""
    role = await session.get(Role, role_id)
    if role is None:
        return None
    if role.code in BUILTIN_CODES:
        return "BUILTIN"
    await session.execute(delete(user_roles).where(user_roles.c.role_id == role_id))
    await session.execute(
        delete(AppAuthorization).where(
            AppAuthorization.principal_type == "role",
            AppAuthorization.principal_id == role_id,
        )
    )
    await session.delete(role)
    return "OK"


async def role_member_count(session: AsyncSession, role_id: int) -> int:
    return await session.scalar(
        select(func.count()).select_from(user_roles).where(user_roles.c.role_id == role_id)
    ) or 0

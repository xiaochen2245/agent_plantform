"""授权域：角色解析 + App 可见性（三态并集，设计 §4.3）。

权限一律以 user_roles 为准；users.roles 旧 JSON 列仅作迁移兜底。
"""
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_authorization import AppAuthorization
from app.models.role import Role, user_roles
from app.models.user import User

PLATFORM_ADMIN = "PLATFORM_ADMIN"


async def role_codes(session: AsyncSession, user: User) -> list[str]:
    """用户角色码（来自 user_roles；空则回退旧 JSON 列，再缺省 USER）。"""
    rows = await session.execute(
        select(Role.code)
        .join(user_roles, user_roles.c.role_id == Role.id)
        .where(user_roles.c.user_id == user.id)
    )
    codes = [r[0] for r in rows]
    if codes:
        return codes
    return list(user.roles or ["USER"])  # 迁移前兜底


async def is_platform_admin(session: AsyncSession, user: User) -> bool:
    return PLATFORM_ADMIN in await role_codes(session, user)


def _role_ids_stmt(user_id: int):
    return select(user_roles.c.role_id).where(user_roles.c.user_id == user_id)


async def resolve_visible_app_ids(session: AsyncSession, user: User) -> set[int] | None:
    """可见 App 集合；None = 不限（PLATFORM_ADMIN）。"""
    if await is_platform_admin(session, user):
        return None

    conditions = [
        and_(
            AppAuthorization.principal_type == "user",
            AppAuthorization.principal_id == user.id,
        )
    ]
    if user.dept_id is not None:
        conditions.append(
            and_(
                AppAuthorization.principal_type == "dept",
                AppAuthorization.principal_id == user.dept_id,
            )
        )
    conditions.append(
        and_(
            AppAuthorization.principal_type == "role",
            AppAuthorization.principal_id.in_(_role_ids_stmt(user.id)),
        )
    )
    rows = await session.execute(
        select(AppAuthorization.app_id).where(or_(*conditions))
    )
    return {r[0] for r in rows}


async def is_authorized(session: AsyncSession, user: User, app_id: int) -> bool:
    visible = await resolve_visible_app_ids(session, user)
    return visible is None or app_id in visible

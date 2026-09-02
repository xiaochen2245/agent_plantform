"""Admin 用户管理业务：列表/创建/更新/重置密码/用户级 App 授权。"""
import secrets
import string
from datetime import datetime, timezone

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.app_authorization import AppAuthorization
from app.models.dataset_authorization import DatasetAuthorization
from app.models.department import Department
from app.models.message import Message  # noqa: F401
from app.models.refresh_token import RefreshToken
from app.models.role import Role, user_roles
from app.models.user import User


def _now() -> datetime:
    return datetime.now(timezone.utc)


def random_password(length: int = 8) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def _role_map(session: AsyncSession, user_ids: list[int]) -> dict[int, list[str]]:
    """user_id → 角色码列表（一批查完，避免 N+1）。"""
    if not user_ids:
        return {}
    rows = await session.execute(
        select(user_roles.c.user_id, Role.code)
        .join(Role, Role.id == user_roles.c.role_id)
        .where(user_roles.c.user_id.in_(user_ids))
        .order_by(Role.id)
    )
    out: dict[int, list[str]] = {}
    for uid, code in rows:
        out.setdefault(uid, []).append(code)
    return out


async def _dept_map(session: AsyncSession, dept_ids: list[int]) -> dict[int, str]:
    ids = [d for d in dept_ids if d is not None]
    if not ids:
        return {}
    rows = await session.execute(
        select(Department.id, Department.name).where(Department.id.in_(ids))
    )
    return {r[0]: r[1] for r in rows}


def _iso(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:  # SQLite 返回 naive，统一补 UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def user_out(user: User, dept_name: str | None, roles: list[str]) -> dict:
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "dept": dept_name,
        "dept_id": user.dept_id,  # 行菜单「设置部门」回显用（名称可重名，id 才可靠）
        "roles": roles or ["USER"],
        "status": user.status,
        "created_at": _iso(user.created_at),
    }


async def list_users(
    session: AsyncSession,
    query: str | None = None,
    status: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[int, list[dict]]:
    stmt = select(User)
    count_stmt = select(func.count()).select_from(User)
    if query:
        like = f"%{query}%"
        cond = or_(User.name.ilike(like), User.email.ilike(like))
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)
    if status is not None:
        stmt = stmt.where(User.status == status)
        count_stmt = count_stmt.where(User.status == status)

    total = await session.scalar(count_stmt) or 0
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    rows = (
        await session.execute(
            stmt.order_by(User.id).offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()

    ids = [u.id for u in rows]
    roles_map = await _role_map(session, ids)
    dept_map = await _dept_map(session, [u.dept_id for u in rows])
    return total, [
        user_out(u, dept_map.get(u.dept_id or -1), roles_map.get(u.id, [])) for u in rows
    ]


async def create_user(session: AsyncSession, payload: dict) -> dict | None:
    """已存在同邮箱返回 None。roles 校验由 router 完成后的 _set_roles 处理。"""
    email = str(payload["email"]).lower()
    if await session.scalar(select(User).where(User.email == email)):
        return None
    user = User(
        email=email,
        name=payload["name"],
        password_hash=hash_password(payload["password"]),
        roles=payload.get("roles") or ["USER"],  # JSON 快照
        dept_id=payload.get("dept_id"),
    )
    session.add(user)
    await session.flush()
    await _set_roles(session, user, payload.get("roles") or ["USER"])
    dept_map = await _dept_map(session, [user.dept_id])
    return user_out(user, dept_map.get(user.dept_id or -1), payload.get("roles") or ["USER"])


async def _set_roles(session: AsyncSession, user: User, codes: list[str]) -> None:
    """全量替换 user_roles；未知角色码直接抛 ValueError（router 转 400）。"""
    roles = (
        await session.execute(select(Role).where(Role.code.in_(codes)))
    ).scalars().all()
    found = {r.code for r in roles}
    unknown = set(codes) - found
    if unknown:
        raise ValueError(f"Unknown roles: {sorted(unknown)}")
    await session.execute(delete(user_roles).where(user_roles.c.user_id == user.id))
    for role in roles:
        await session.execute(
            user_roles.insert().values(user_id=user.id, role_id=role.id)
        )
    user.roles = codes  # JSON 快照同步


async def update_user(
    session: AsyncSession, user_id: int, payload: dict, admin_id: int
) -> dict | None:
    """返回更新后的 user_out；不存在 None；admin 自禁返回 'SELF_DISABLE'。"""
    user = await session.get(User, user_id)
    if user is None:
        return None
    if payload.get("status") == 0 and user_id == admin_id:
        return "SELF_DISABLE"  # type: ignore[return-value]
    if "name" in payload and payload["name"] is not None:
        user.name = payload["name"]
    if "dept_id" in payload and payload["dept_id"] is not None:
        user.dept_id = payload["dept_id"]
    if payload.get("status") is not None:
        user.status = payload["status"]
    if payload.get("roles") is not None:
        await _set_roles(session, user, payload["roles"])
    await session.flush()
    roles = (await _role_map(session, [user.id])).get(user.id, [])
    dept_map = await _dept_map(session, [user.dept_id])
    return user_out(user, dept_map.get(user.dept_id or -1), roles)


async def reset_password(session: AsyncSession, user_id: int) -> str | None:
    user = await session.get(User, user_id)
    if user is None:
        return None
    new_password = random_password()
    user.password_hash = hash_password(new_password)
    # 失效其全部 refresh token（契约：reset_password 同步踢下线）
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
    await session.flush()
    return new_password


async def get_user_apps(session: AsyncSession, user_id: int) -> list[int] | None:
    rows = await session.execute(
        select(AppAuthorization.app_id).where(
            AppAuthorization.principal_type == "user",
            AppAuthorization.principal_id == user_id,
        )
    )
    return sorted({r[0] for r in rows})


async def set_user_apps(
    session: AsyncSession, user_id: int, app_ids: list[int]
) -> list[int] | None:
    """用户级授权全量替换；用户不存在 None；未知 App 返回 'UNKNOWN_APPS'。"""
    from app.models.app import App as AppModel

    if await session.get(User, user_id) is None:
        return None
    known = set(
        r[0]
        for r in await session.execute(select(AppModel.id).where(AppModel.id.in_(app_ids)))
    )
    if set(app_ids) - known:
        return "UNKNOWN_APPS"  # type: ignore[return-value]
    await session.execute(
        delete(AppAuthorization).where(
            AppAuthorization.principal_type == "user",
            AppAuthorization.principal_id == user_id,
        )
    )
    for app_id in sorted(set(app_ids)):
        session.add(
            AppAuthorization(
                app_id=app_id, principal_type="user", principal_id=user_id
            )
        )
    await session.flush()
    return sorted(set(app_ids))


async def get_user_datasets(session: AsyncSession, user_id: int) -> list[str] | None:
    """用户级知识库授权（契约 v8）；用户不存在 None。"""
    if await session.get(User, user_id) is None:
        return None
    rows = await session.execute(
        select(DatasetAuthorization.dataset_id).where(
            DatasetAuthorization.principal_type == "user",
            DatasetAuthorization.principal_id == user_id,
        )
    )
    return sorted({r[0] for r in rows})

async def set_user_datasets(
    session: AsyncSession, user_id: int, dataset_ids: list[str]
) -> list[str] | None:
    """用户级知识库授权全量替换（契约 v8）。

    dataset_id 为 Dify 侧 UUID，网关不校验存在性（Dify 是真相源；前端仅从
    实际列表勾选，非法 id 只会变成永不匹配的授权行，无害）。
    """
    if await session.get(User, user_id) is None:
        return None
    await session.execute(
        delete(DatasetAuthorization).where(
            DatasetAuthorization.principal_type == "user",
            DatasetAuthorization.principal_id == user_id,
        )
    )
    for dataset_id in sorted(set(dataset_ids)):
        session.add(
            DatasetAuthorization(
                dataset_id=dataset_id, principal_type="user", principal_id=user_id
            )
        )
    await session.flush()
    return sorted(set(dataset_ids))

# ── 三态授权（user / dept / role）共享函数 ───────────────────────────────────

_VALID_PRINCIPAL_TYPES = {"user", "dept", "role"}


async def _principal_exists(
    session: AsyncSession, principal_type: str, principal_id: int
) -> bool:
    """按 principal_type 校验主体存在；非法类型直接 False。"""
    if principal_type == "user":
        return (await session.get(User, principal_id)) is not None
    if principal_type == "dept":
        return (await session.get(Department, principal_id)) is not None
    if principal_type == "role":
        return (await session.get(Role, principal_id)) is not None
    return False


async def get_principal_apps(
    session: AsyncSession, principal_type: str, principal_id: int
) -> list[int] | None:
    """返回该主体已授权 app_id 列表；主体不存在 → None。"""
    if principal_type not in _VALID_PRINCIPAL_TYPES:
        return None
    if not await _principal_exists(session, principal_type, principal_id):
        return None
    rows = await session.execute(
        select(AppAuthorization.app_id).where(
            AppAuthorization.principal_type == principal_type,
            AppAuthorization.principal_id == principal_id,
        )
    )
    return sorted({r[0] for r in rows})

async def set_principal_apps(
    session: AsyncSession, principal_type: str, principal_id: int, app_ids: list[int]
) -> list[int] | str | None:
    """全量替换主体授权。

    返回：
    - None   主体不存在
    - 'UNKNOWN_APPS' 部分 app_id 不存在
    - 'INVALID_PRINCIPAL' 非法 principal_type
    - list   替换后的 app_ids（已排序去重）
    """
    if principal_type not in _VALID_PRINCIPAL_TYPES:
        return "INVALID_PRINCIPAL"
    if not await _principal_exists(session, principal_type, principal_id):
        return None
    from app.models.app import App as AppModel

    known = set(
        r[0]
        for r in await session.execute(
            select(AppModel.id).where(AppModel.id.in_(app_ids))
        )
    )
    if set(app_ids) - known:
        return "UNKNOWN_APPS"
    await session.execute(
        delete(AppAuthorization).where(
            AppAuthorization.principal_type == principal_type,
            AppAuthorization.principal_id == principal_id,
        )
    )
    for app_id in sorted(set(app_ids)):
        session.add(
            AppAuthorization(
                app_id=app_id,
                principal_type=principal_type,
                principal_id=principal_id,
            )
        )
    await session.flush()
    return sorted(set(app_ids))

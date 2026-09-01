"""建表 + dev 种子 + 存量角色迁移（lifespan 与测试共用）。

种子内容：
- 角色 USER / PLATFORM_ADMIN
- 管理员用户（roles JSON 同时写入，兼容迁移幂等）
- 3 个 Agent 镜像（契约 §Apps，id 显式固定）
- 3 个 App → 角色 USER 的授权（全员可见，保持既有 E2E 体验）

存量迁移：users.roles JSON → user_roles 行（JSON 列保留作历史快照，
此后权限一律以 user_roles 为准，见 app/authz.py）。
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models import App, RefreshToken, User  # noqa: F401 RefreshToken 触发表注册
from app.models import AppAuthorization, Role, user_roles

# 契约 docs/api-contract.md §Apps 的三个 Agent（id 显式固定对齐契约）
SEED_APPS: list[tuple[int, str, str, str, str]] = [
    (1, "app-test-001", "IT 运维助手", "解答服务器、网络与账号问题", "chat"),
    (2, "app-test-002", "报销政策问答", "差旅与报销规则查询", "chat"),
    (3, "app-test-003", "代码评审助手", "MR 预审与规范检查", "agent"),
]

SEED_ROLES: list[tuple[int, str, str]] = [
    (1, "USER", "普通用户"),
    (2, "PLATFORM_ADMIN", "平台管理员"),
]


async def init_db(drop: bool = False) -> None:
    """create_all + 种子 + 角色迁移。

    drop=True 仅供测试重建库；生产路径（lifespan）只增量建表，
    并将被 Alembic 迁移替换。
    """
    async with engine.begin() as conn:
        if drop:
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as session:  # type: AsyncSession
        await _seed_roles(session)
        await _seed_admin(session)
        await _seed_apps(session)
        await _migrate_json_roles(session)
        await _seed_user_role_grants(session)
        await session.commit()


async def _seed_roles(session: AsyncSession) -> None:
    for role_id, code, name in SEED_ROLES:
        if await session.scalar(select(Role).where(Role.code == code)) is None:
            session.add(Role(id=role_id, code=code, name=name))
    await session.flush()


async def _seed_admin(session: AsyncSession) -> None:
    if await session.scalar(select(User).limit(1)) is None:
        session.add(
            User(
                email=settings.SEED_ADMIN_EMAIL,
                name="平台管理员",
                password_hash=hash_password(settings.SEED_ADMIN_PASSWORD),
                roles=["USER", "PLATFORM_ADMIN"],  # JSON 历史快照；权限走 user_roles
                dept_id=None,
            )
        )
        await session.flush()


async def _seed_apps(session: AsyncSession) -> None:
    if await session.scalar(select(App).limit(1)) is None:
        for app_id, dify_id, name, description, mode in SEED_APPS:
            session.add(
                App(
                    id=app_id,
                    dify_app_id=dify_id,
                    name=name,
                    description=description,
                    mode=mode,
                )
            )
        await session.flush()


async def _migrate_json_roles(session: AsyncSession) -> None:
    """users.roles JSON → user_roles（幂等；空则补默认 USER）。"""
    users = (await session.execute(select(User))).scalars().all()
    for user in users:
        codes = user.roles or ["USER"]
        role_rows = (
            await session.execute(select(Role).where(Role.code.in_(codes)))
        ).scalars().all()
        existing = set(
            await session.execute(
                select(user_roles.c.role_id).where(user_roles.c.user_id == user.id)
            )
        )
        existing_ids = {r[0] for r in existing}
        for role in role_rows:
            if role.id not in existing_ids:
                await session.execute(
                    user_roles.insert().values(user_id=user.id, role_id=role.id)
                )
        if not existing_ids and not role_rows:
            # JSON 里是未知角色码：至少保住 USER，避免用户无角色
            user_role = await session.scalar(select(Role).where(Role.code == "USER"))
            if user_role is not None:
                await session.execute(
                    user_roles.insert().values(user_id=user.id, role_id=user_role.id)
                )


async def _seed_user_role_grants(session: AsyncSession) -> None:
    """3 个 App 授给角色 USER（全员可见；幂等）。"""
    user_role = await session.scalar(select(Role).where(Role.code == "USER"))
    if user_role is None:
        return
    for app_id, *_ in SEED_APPS:
        exists = await session.scalar(
            select(AppAuthorization).where(
                AppAuthorization.app_id == app_id,
                AppAuthorization.principal_type == "role",
                AppAuthorization.principal_id == user_role.id,
            )
        )
        if exists is None:
            session.add(
                AppAuthorization(
                    app_id=app_id,
                    principal_type="role",
                    principal_id=user_role.id,
                )
            )


async def dispose_engine() -> None:
    await engine.dispose()

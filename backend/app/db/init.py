"""建表 + dev 种子（用户 + 3 个 Agent 镜像；lifespan 与测试共用）。"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models import App, RefreshToken, User  # noqa: F401 RefreshToken 触发表注册

# 契约 docs/api-contract.md §Apps 的三个 Agent（id 显式固定对齐契约）
SEED_APPS: list[tuple[int, str, str, str, str]] = [
    (1, "app-test-001", "IT 运维助手", "解答服务器、网络与账号问题", "chat"),
    (2, "app-test-002", "报销政策问答", "差旅与报销规则查询", "chat"),
    (3, "app-test-003", "代码评审助手", "MR 预审与规范检查", "agent"),
]


async def init_db(drop: bool = False) -> None:
    """create_all + 无用户时种入管理员。

    drop=True 仅供测试重建库；生产路径（lifespan）只增量建表，
    并将被 Alembic 迁移替换。
    """
    async with engine.begin() as conn:
        if drop:
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as session:  # type: AsyncSession
        existing = await session.scalar(select(User).limit(1))
        if existing is None:
            session.add(
                User(
                    email=settings.SEED_ADMIN_EMAIL,
                    name="平台管理员",
                    password_hash=hash_password(settings.SEED_ADMIN_PASSWORD),
                    roles=["USER", "PLATFORM_ADMIN"],
                    dept_id=None,
                )
            )
            await session.commit()

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
            await session.commit()


async def dispose_engine() -> None:
    await engine.dispose()

"""建表 + dev 种子用户（lifespan 与测试共用）。"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models.refresh_token import RefreshToken  # noqa: F401 触发表注册
from app.models.user import User


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


async def dispose_engine() -> None:
    await engine.dispose()

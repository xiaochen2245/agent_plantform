"""异步 engine / session 工厂 / get_db 依赖。

SQLite（默认 dev）使用 StaticPool：内存库全连接共享同一实例，
否则每个新连接都会得到一个空库。
"""
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.core.config import settings

_url = settings.DATABASE_URL
_is_sqlite = _url.startswith("sqlite")

engine = create_async_engine(
    _url,
    echo=False,
    # ponytail: StaticPool 单连接——SQLite/内存库正确性优先；Postgres 时走默认池
    poolclass=StaticPool if _is_sqlite else None,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

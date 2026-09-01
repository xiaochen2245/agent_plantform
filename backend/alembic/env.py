"""Alembic 环境（异步引擎模式）。

URL 优先级：cfg.set_main_option 显式覆盖 > settings.DATABASE_URL。
被 app/db/init.py 的启动引导与 scripts/migrate.sh 共用。
"""
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import settings
from app.db.base import Base
from app.models import (  # noqa: F401  触发全部模型注册进 metadata
    App,
    AppAuthorization,
    Conversation,
    Department,
    Message,
    RefreshToken,
    Role,
    User,
    user_roles,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

_PLACEHOLDER = "driver://"
_url = config.get_main_option("sqlalchemy.url")
if not _url or _url.startswith(_PLACEHOLDER):
    config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())

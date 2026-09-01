"""迁移烟雾：全新临时库跑 alembic upgrade head 后关键表齐备。

用文件库而非内存库：alembic env.py 自建引擎（NullPool），
内存 SQLite 每连接独立实例，跨连接不可见。
"""
import sqlite3
import subprocess
import sys

from alembic import command
from alembic.config import Config


def test_baseline_upgrade_creates_schema(tmp_path):
    db = tmp_path / "mig_smoke.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db}")

    command.upgrade(cfg, "head")

    con = sqlite3.connect(db)
    try:
        tables = {
            row[0]
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        cols = {row[1] for row in con.execute("PRAGMA table_info(apps)")}
    finally:
        con.close()

    expected = {
        "alembic_version",
        "users",
        "roles",
        "user_roles",
        "departments",
        "apps",
        "app_authorizations",
        "conversations",
        "messages",
        "refresh_tokens",
    }
    assert expected <= tables, f"missing: {expected - tables}"
    assert "inputs_schema" in cols


def test_downgrade_to_base_and_reupgrade(tmp_path):
    db = tmp_path / "mig_cycle.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db}")

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")  # 可重放

    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT count(*) FROM alembic_version").fetchone()[0] == 1
    finally:
        con.close()


_BOOTSTRAP_SCRIPT = """
import asyncio, logging, sys
logging.basicConfig(level=logging.INFO)
from app.db.init import init_db
asyncio.run(init_db())          # 运行时路径：无 drop，应走 alembic
from sqlalchemy import select
from app.models import App, User
async def verify():
    from app.db.session import SessionLocal
    async with SessionLocal() as s:
        assert (await s.execute(select(App))).scalars().all().__len__() == 4
        assert (await s.execute(select(User))).scalars().all().__len__() == 1
asyncio.run(verify())
print("BOOTSTRAP_OK")
"""


def test_lifespan_bootstrap_prefers_alembic(tmp_path):
    """文件库引导：子进程运行（避免模块 reload 污染同进程测试），断言 alembic 路径成功 + 种子写入。"""
    db = tmp_path / "bootstrap.db"
    import os

    env = dict(os.environ, DATABASE_URL=f"sqlite+aiosqlite:///{db}")
    proc = subprocess.run(
        [sys.executable, "-c", _BOOTSTRAP_SCRIPT],
        capture_output=True,
        text=True,
        env=env,
        cwd=".",
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "BOOTSTRAP_OK" in proc.stdout
    # alembic 路径证据：alembic_version 表存在（create_all 回退不会建它）
    con = sqlite3.connect(db)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "alembic_version" in tables
    finally:
        con.close()

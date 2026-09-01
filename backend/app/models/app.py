"""App 模型（Dify 应用本地镜像；对外展示名即 Agent）。

dify_app_id 与 API key 的真实绑定后续入库加密（设计 §7.1），
本切片 key 走 env DIFY_API_KEY_APP_<id>。
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# SQLite 需 INTEGER 主键才能自增；Postgres 用 BIGINT
PK = BigInteger().with_variant(Integer, "sqlite")


class App(Base):
    __tablename__ = "apps"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    dify_app_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    mode: Mapped[str] = mapped_column(String(20), default="chat", nullable=False)  # chat / agent / workflow / completion
    status: Mapped[int] = mapped_column(default=1, nullable=False)  # 1 启用 / 0 下架
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

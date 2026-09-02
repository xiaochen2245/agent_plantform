"""FastAPI 应用入口：lifespan（建表+种子+告警+清理）、CORS（仅 DEBUG）、CSRF、路由。"""
import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.admin.router import router as admin_router
from app.apps.router import router as apps_router
from app.auth.router import router as auth_router
from app.chat.router import router as chat_router
from app.core.config import settings
from app.core.middleware import CSRFMiddleware
from app.conversations.router import router as conversations_router
from app.db.init import dispose_engine, init_db
from app.db.session import SessionLocal
from app.dify.client import DifyClient, app_api_key
from app.files.cleanup import sweep_expired_uploads
from app.files.router import router as files_router
from app.models.app import App

_logger = logging.getLogger("app.main")


async def _warn_insecure_startup_config() -> None:
    """B3/B4：不安全配置的启动告警（仅日志，不阻断启动）。"""
    # B4：生产默认管理员密码
    if not settings.DEBUG and settings.SEED_ADMIN_PASSWORD == "admin123":
        _logger.error(
            "生产环境仍在使用默认管理员密码（SEED_ADMIN_PASSWORD 未覆盖）——请立即通过环境变量修改"
        )
    # B3：回退 demo-key 的应用（真实调用会被上游拒绝，易被误认为平台故障）
    try:
        async with SessionLocal() as session:
            app_ids = list((await session.execute(select(App.id))).scalars())
    except Exception:
        return  # 告警自身失败静默：不阻断启动
    demo_apps = [i for i in app_ids if app_api_key(i) == "demo-key"]
    if demo_apps:
        _logger.warning(
            "以下应用未配置 Dify API Key（当前回退 demo-key，上游将拒绝调用）："
            "app ids=%s —— 请设置 DIFY_API_KEY_APP_<id>",
            demo_apps,
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await init_db()
    await _warn_insecure_startup_config()
    # B5：上传 TTL 清理（fire-and-forget；持有引用防 GC）
    app.state.upload_sweep_task = asyncio.create_task(sweep_expired_uploads())
    app.state.dify = DifyClient()  # 进程级单例（设计 §13.1）
    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            app.state.upload_sweep_task.cancel()
        await app.state.dify.aclose()
        await dispose_engine()


app = FastAPI(title=settings.APP_NAME, version="0.1.0", lifespan=lifespan)

# CORS 仅开发模式（生产走 Nginx 同源反代，无需 CORS）
if settings.DEBUG:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.add_middleware(CSRFMiddleware)

app.include_router(auth_router)
app.include_router(apps_router)
app.include_router(chat_router)
app.include_router(files_router)
app.include_router(conversations_router)
app.include_router(admin_router)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "version": "0.1.0"}

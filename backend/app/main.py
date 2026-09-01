"""FastAPI 应用入口：lifespan（建表+种子）、CORS（仅 DEBUG）、CSRF、路由。"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.admin.router import router as admin_router
from app.apps.router import router as apps_router
from app.auth.router import router as auth_router
from app.chat.router import router as chat_router
from app.core.config import settings
from app.core.middleware import CSRFMiddleware
from app.conversations.router import router as conversations_router
from app.db.init import dispose_engine, init_db
from app.dify.client import DifyClient


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await init_db()
    app.state.dify = DifyClient()  # 进程级单例（设计 §13.1）
    try:
        yield
    finally:
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
app.include_router(conversations_router)
app.include_router(admin_router)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "version": "0.1.0"}

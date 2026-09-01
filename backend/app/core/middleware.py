"""CSRF 防护：写方法 + /api/* 时校验 Origin 白名单。

语义（docs/api-contract.md）：
- 浏览器跨站写请求必然携带 Origin；Origin 存在且不在白名单 → 403
- Origin 缺失（curl / 服务间调用 / 测试）→ 放行，SameSite=Strict 仍是主防线
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import settings

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _allowed_origins() -> set[str]:
    allowed = {o.rstrip("/") for o in settings.allowed_origins_list}
    if settings.DEBUG:
        allowed.update(
            {
                "http://localhost:5173",
                "http://127.0.0.1:5173",
                "http://localhost:8000",
                "http://127.0.0.1:8000",
            }
        )
    return allowed


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method in WRITE_METHODS and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin", "").rstrip("/")
            if origin and origin not in _allowed_origins():
                return JSONResponse(
                    {"detail": "Forbidden: invalid origin"}, status_code=403
                )
        return await call_next(request)

"""认证路由：login / refresh / logout / me（契约：docs/api-contract.md）。"""
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.auth.service import (
    authenticate,
    issue_tokens,
    revoke_refresh,
    rotate_refresh,
)
from app.core.config import settings
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, MeResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])

ACCESS_COOKIE = "access_token_cookie"
REFRESH_COOKIE = "refresh_token_cookie"


def _cookie_kwargs() -> dict:
    # dev(HTTP) 下 secure=False；生产 DEBUG=false 自动收紧
    return {"httponly": True, "samesite": "strict", "secure": not settings.DEBUG, "path": "/"}


def _set_auth_cookies(response: Response, access: str, raw_refresh: str) -> None:
    response.set_cookie(
        ACCESS_COOKIE,
        access,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        **_cookie_kwargs(),
    )
    response.set_cookie(
        REFRESH_COOKIE,
        raw_refresh,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        **_cookie_kwargs(),
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/")


@router.post("/login")
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    user = await authenticate(db, body.email, body.password)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    access, raw_refresh = await issue_tokens(db, user)
    resp = Response(status_code=200)
    _set_auth_cookies(resp, access, raw_refresh)
    return resp


@router.post("/refresh")
async def refresh(request: Request, db: AsyncSession = Depends(get_db)):
    raw = request.cookies.get(REFRESH_COOKIE)
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No refresh token")
    rotated = await rotate_refresh(db, raw)
    if rotated is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    _, access, new_raw = rotated
    resp = Response(status_code=200)
    _set_auth_cookies(resp, access, new_raw)
    return resp


@router.post("/logout")
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    raw = request.cookies.get(REFRESH_COOKIE)
    if raw:
        await revoke_refresh(db, raw)
    resp = Response(status_code=200)
    _clear_auth_cookies(resp)
    return resp


@router.get("/me", response_model=MeResponse)
async def me(user: User = Depends(current_user)):
    return MeResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        roles=user.roles or ["USER"],
        dept_id=user.dept_id,
    )

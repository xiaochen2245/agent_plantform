"""FastAPI 依赖：当前用户（access JWT cookie → User）与平台管理员门。"""
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import is_platform_admin
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User


async def current_user(
    request: Request, db: AsyncSession = Depends(get_db)
) -> User:
    token = request.cookies.get("access_token_cookie")
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    try:
        user_id = int(payload["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    user = await db.get(User, user_id)
    if user is None or user.status != 1:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or disabled")
    return user


async def require_platform_admin(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> User:
    """仅 PLATFORM_ADMIN；实时查 user_roles（不信任签发时的 JWT roles 快照）。"""
    if not await is_platform_admin(db, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Platform admin only")
    return user

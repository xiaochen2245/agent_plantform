"""认证业务：校验、签发、refresh 轮转、撤销。"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_refresh_token,
    verify_password,
)
from app.authz import role_codes
from app.models.refresh_token import RefreshToken
from app.models.user import User


async def authenticate(session: AsyncSession, email: str, password: str) -> User | None:
    user = await session.scalar(select(User).where(User.email == email))
    if user is None or user.status != 1:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


async def issue_tokens(session: AsyncSession, user: User) -> tuple[str, str]:
    """签发 (access_jwt, raw_refresh)。refresh 哈希入库；roles 取自 user_roles。"""
    roles = await role_codes(session, user)
    access = create_access_token(user_id=user.id, roles=roles, dept_id=user.dept_id)
    raw_refresh, token_hash = create_refresh_token()
    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=datetime.now(timezone.utc)
            + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        )
    )
    await session.flush()
    return access, raw_refresh


async def revoke_refresh(session: AsyncSession, raw_refresh: str) -> bool:
    token_hash = hash_refresh_token(raw_refresh)
    rt = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    if rt is None or rt.revoked_at is not None:
        return False
    rt.revoked_at = datetime.now(timezone.utc)
    await session.flush()
    return True


async def rotate_refresh(
    session: AsyncSession, raw_refresh: str
) -> tuple[User, str, str] | None:
    """校验并轮转 refresh：旧的撤销，返回 (user, new_access, new_raw_refresh)。"""
    token_hash = hash_refresh_token(raw_refresh)
    rt = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    now = datetime.now(timezone.utc)
    if rt is None or rt.revoked_at is not None:
        return None
    # SQLite 存回 naive datetime（DateTime(timezone=True) 不生效），统一补 UTC
    expires = (
        rt.expires_at
        if rt.expires_at.tzinfo is not None
        else rt.expires_at.replace(tzinfo=timezone.utc)
    )
    if expires < now:
        return None
    user = await session.get(User, rt.user_id)
    if user is None or user.status != 1:
        return None
    rt.revoked_at = now
    access, new_raw = await issue_tokens(session, user)
    return user, access, new_raw

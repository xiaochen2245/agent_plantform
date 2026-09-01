"""Apps 路由：GET /api/apps/me（三态授权并集过滤，设计 §4.3）。"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.authz import resolve_visible_app_ids
from app.db.session import get_db
from app.models.app import App
from app.models.user import User
from app.schemas.chat import AppsResponse

router = APIRouter(prefix="/api/apps", tags=["apps"])


@router.get("/me", response_model=AppsResponse)
async def my_apps(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> AppsResponse:
    visible = await resolve_visible_app_ids(db, user)  # None = 管理员不限
    stmt = select(App).where(App.status == 1).order_by(App.id)
    if visible is not None:
        stmt = stmt.where(App.id.in_(visible or {-1}))
    rows = (await db.execute(stmt)).scalars().all()
    return AppsResponse(
        apps=[
            {
                "id": r.id,
                "name": r.name,
                "description": r.description,
                "mode": r.mode,
                "inputs_schema": r.inputs_schema,
            }
            for r in rows
        ]
    )

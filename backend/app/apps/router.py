"""Apps 路由：GET /api/apps/me。

MVP 授权 = 登录即全部可见；app_authorizations 三态过滤属后续切片。
"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
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
    rows = (await db.execute(select(App).where(App.status == 1).order_by(App.id))).scalars().all()
    return AppsResponse(
        apps=[
            {"id": r.id, "name": r.name, "description": r.description, "mode": r.mode}
            for r in rows
        ]
    )

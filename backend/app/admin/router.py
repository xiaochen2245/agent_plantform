"""Admin 路由：用户管理 + 用户级 App 授权（仅 PLATFORM_ADMIN，契约 v2 §Admin）。"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import service
from app.auth.deps import require_platform_admin
from app.db.session import get_db
from app.models.user import User
from app.schemas.admin import (
    AdminUserCreate,
    AdminUserUpdate,
    AdminUsersResponse,
    ResetPasswordResponse,
    UserAppsResponse,
    UserAppsUpdate,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/users", response_model=AdminUsersResponse)
async def list_users(
    query: str | None = None,
    status: int | None = None,
    page: int = 1,
    page_size: int = 20,
    _admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminUsersResponse:
    total, items = await service.list_users(db, query, status, page, page_size)
    return AdminUsersResponse(total=total, items=items)  # type: ignore[arg-type]


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(
    body: AdminUserCreate,
    _admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    created = await service.create_user(
        db,
        {
            "name": body.name,
            "email": body.email,
            "password": body.password,
            "dept_id": body.dept_id,
            "roles": body.roles,
        },
    )
    if created is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already exists")
    await db.commit()
    return created


@router.patch("/users/{user_id}")
async def update_user(
    user_id: int,
    body: AdminUserUpdate,
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    payload = body.model_dump(exclude_unset=True)
    try:
        updated = await service.update_user(db, user_id, payload, admin_id=admin.id)
    except ValueError as e:  # 未知角色码
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if updated == "SELF_DISABLE":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot disable yourself")
    await db.commit()
    return updated


@router.post("/users/{user_id}/reset_password", response_model=ResetPasswordResponse)
async def reset_password(
    user_id: int,
    _admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    new_password = await service.reset_password(db, user_id)
    if new_password is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    await db.commit()
    return ResetPasswordResponse(password=new_password)


@router.get("/users/{user_id}/apps", response_model=UserAppsResponse)
async def get_user_apps(
    user_id: int,
    _admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    app_ids = await service.get_user_apps(db, user_id)
    if app_ids is None:
        app_ids = []
    return UserAppsResponse(app_ids=app_ids)


@router.put("/users/{user_id}/apps", response_model=UserAppsResponse)
async def set_user_apps(
    user_id: int,
    body: UserAppsUpdate,
    _admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await service.set_user_apps(db, user_id, body.app_ids)
    if result == "UNKNOWN_APPS":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown app ids")
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    await db.commit()
    return UserAppsResponse(app_ids=result)

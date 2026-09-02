"""角色管理路由（仅 PLATFORM_ADMIN）。"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_platform_admin
from app.db.session import get_db
from app.models.user import User
from app.roles import service
from app.roles.schemas import RoleCreate, RoleUpdate, RolesResponse

router = APIRouter(prefix="/api/admin/roles", tags=["admin"])


@router.get("", response_model=RolesResponse)
async def list_roles(
    _admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> RolesResponse:
    items = await service.list_roles(db)
    return RolesResponse(items=items)  # type: ignore[arg-type]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_role(
    body: RoleCreate,
    _admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    created = await service.create_role(db, body.code, body.name)
    if isinstance(created, str):
        raise HTTPException(status.HTTP_409_CONFLICT, created.lower())
    await db.commit()
    return created


@router.patch("/{role_id}")
async def update_role(
    role_id: int,
    body: RoleUpdate,
    _admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    payload = body.model_dump(exclude_unset=True)
    updated = await service.update_role(db, role_id, payload)
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role not found")
    await db.commit()
    return updated


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    role_id: int,
    _admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await service.delete_role(db, role_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role not found")
    if result == "BUILTIN":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "builtin roles (USER, PLATFORM_ADMIN) cannot be deleted",
        )
    await db.commit()
    return None

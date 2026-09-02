"""部门管理路由（仅 PLATFORM_ADMIN，契约 v2 §Admin 补齐）。

错误码约定：
- 404：部门不存在
- 400：SELF_PARENT / UNKNOWN_PARENT / CYCLE / 校验失败
- 409：HAS_CHILDREN / HAS_USERS
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_platform_admin
from app.db.session import get_db
from app.depts import service
from app.models.user import User
from app.depts.schemas import (
    DeptCreate,
    DeptUpdate,
    DeptsResponse,
)

router = APIRouter(prefix="/api/admin/depts", tags=["admin"])


@router.get("", response_model=DeptsResponse)
async def list_depts(
    _admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> DeptsResponse:
    items = await service.list_depts(db)
    return DeptsResponse(items=items)  # type: ignore[arg-type]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_dept(
    body: DeptCreate,
    _admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    created = await service.create_dept(db, body.name, body.parent_id)
    if isinstance(created, str):  # UNKNOWN_PARENT
        raise HTTPException(status.HTTP_400_BAD_REQUEST, created.lower())
    await db.commit()
    return created


@router.patch("/{dept_id}")
async def update_dept(
    dept_id: int,
    body: DeptUpdate,
    _admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    payload = body.model_dump(exclude_unset=True)
    result = await service.update_dept(db, dept_id, payload)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Department not found")
    if isinstance(result, str):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, result.lower())
    await db.commit()
    return result


@router.delete("/{dept_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dept(
    dept_id: int,
    _admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await service.delete_dept(db, dept_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Department not found")
    if result == "HAS_CHILDREN":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "department has children; remove or reparent them first",
        )
    if result == "HAS_USERS":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "department has users; reassign them first",
        )
    await db.commit()
    return None

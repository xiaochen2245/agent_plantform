"""
FastAPI 网关依赖项 (Dependencies)
提供:
1. get_tenant_id: 从 Header (X-Tenant-ID) 或 Query 参数提取租户标识并强校验
2. get_tenant_db: 自动绑定协程 TenantContext 与 PostgreSQL 16+ RLS 运行时会话
"""

from collections.abc import AsyncIterator
from typing import Optional

from fastapi import Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant_context import TenantContext, apply_tenant_rls_session
from app.db.session import get_db


async def get_tenant_id(
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    tenant_id: Optional[str] = Query(None),
) -> str:
    """
    提取并校验多租户标识。
    优先从 Header 'X-Tenant-ID' 读取，其次从 Query 参数读取。
    未指定时默认回退至 'default'。若显式传入空字符串则抛出 400 校验异常。
    """
    if x_tenant_id is not None:
        tid = x_tenant_id.strip()
        if not tid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="租户标识 (X-Tenant-ID) 不能为空",
            )
        return tid

    if tenant_id is not None:
        tid = tenant_id.strip()
        if not tid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="租户标识 (tenant_id) 不能为空",
            )
        return tid

    return "default"


async def get_tenant_db(
    tenant_id: str = Depends(get_tenant_id),
    session: AsyncSession = Depends(get_db),
) -> AsyncIterator[AsyncSession]:
    """
    租户隔离型数据库会话依赖。
    在异步协程内激活 TenantContext 上下文管理器，并在底层会话执行
    SELECT set_config('app.current_tenant_id', :tid, true)
    激活 PostgreSQL 16+ 行级安全策略 (RLS)。
    """
    with TenantContext(tenant_id):
        await apply_tenant_rls_session(session, tenant_id)
        yield session

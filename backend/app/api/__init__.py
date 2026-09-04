"""
API 根包
"""
from app.api.dependencies import get_tenant_db, get_tenant_id

__all__ = ["get_tenant_id", "get_tenant_db"]

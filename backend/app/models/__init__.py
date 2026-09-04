"""模型聚合（导入即注册表；Alembic autogenerate 亦从此发现）。"""
from app.models.app import App
from app.models.app_authorization import AppAuthorization
from app.models.conversation import Conversation
from app.models.department import Department
from app.models.message import Message
from app.models.refresh_token import RefreshToken
from app.models.role import Role, user_roles
from app.models.upload_file import UploadFile
from app.models.user import User

from app.models.audit_rag import (
    AuditTask,
    ChunkLevel,
    DeviationType,
    Document,
    DocumentChunk,
    HistoricalAuditRisk,
    ReviewResult,
    SeverityLevel,
    TaskStatus,
    TaskType,
    Tenant,
    TenantStatus,
)

__all__ = [
    "App",
    "AppAuthorization",
    "Conversation",
    "Department",
    "Message",
    "RefreshToken",
    "Role",
    "UploadFile",
    "User",
    "user_roles",
    "Tenant",
    "TenantStatus",
    "Document",
    "DocumentChunk",
    "ChunkLevel",
    "AuditTask",
    "TaskType",
    "TaskStatus",
    "ReviewResult",
    "DeviationType",
    "SeverityLevel",
    "HistoricalAuditRisk",
]

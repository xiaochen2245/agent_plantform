"""知识库路由（契约 v7/v9）：Dify Knowledge API 的 JSON 代理。

权限：读（列表/文档/命中测试）= 授权用户；写（建库/删库/上传/删除/授权管理）
= PLATFORM_ADMIN。租户隔离见 authz（契约 v8）；写操作一律落审计表（契约 v9）。
边界：App↔知识库绑定只能在 Dify 控制台完成，Service API 无此能力。
"""
import json
import logging

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user, require_platform_admin
from app.authz import is_dataset_authorized, resolve_visible_dataset_ids
from app.chat.service import _summarize_upstream_error
from app.db.session import get_db
from app.dify.client import DifyClient, DifyDatasetError, dataset_api_key
from app.dify.deps import get_dify
from app.files.router import MAX_UPLOAD_BYTES, sanitize_filename
from app.models.dataset_authorization import DatasetAuthorization
from app.models.department import Department
from app.models.kb_audit import KbAuditLog
from app.models.role import Role
from app.models.user import User
from app.schemas.kb import DatasetCreate, GrantCreate, RetrieveQuery, TextDocCreate

router = APIRouter(prefix="/api/kb", tags=["kb"])

_logger = logging.getLogger("app.kb")

# 文档类白名单（比 chat 附件宽：知识库就是吃文档的；不含图片——入库无意义）
KB_DOC_MIMES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/plain",
    "text/markdown",
    "text/csv",
    "text/html",
    "application/json",
}


def _require_dataset_key() -> None:
    if not dataset_api_key():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "knowledge base service not configured"
        )


async def _require_dataset_access(
    db: AsyncSession, user: User, dataset_id: str
) -> None:
    """租户隔离门（契约 v8）：未授权库 → 403（与 chat/send 的 app 门同文案风格）。"""
    if not await is_dataset_authorized(db, user, dataset_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not authorized for this dataset")


def _audit(db: AsyncSession, user: User, action: str, dataset_id: str | None = None, **detail) -> None:
    """写操作审计（契约 v9）：与授权变更同事务提交，单独失败不阻断主流程语义。"""
    db.add(
        KbAuditLog(
            user_id=user.id,
            action=action,
            dataset_id=dataset_id,
            detail=json.dumps(detail, ensure_ascii=False) if detail else None,
        )
    )


_PRINCIPAL_MODELS = {"user": User, "dept": Department, "role": Role}


async def _principal_name(db: AsyncSession, ptype: str, pid: int) -> str | None:
    model = _PRINCIPAL_MODELS[ptype]
    row = await db.get(model, pid)
    if row is None:
        return None
    return str(row.name)


async def _call(coro) -> dict:
    """统一执行 dataset 调用：上游 4xx 原码透传（message 精简），5xx/网络 → 502。"""
    try:
        return await coro
    except DifyDatasetError as e:
        code = e.status_code if 400 <= e.status_code < 500 else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(code, _summarize_upstream_error(e.message))
    except httpx.HTTPError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "knowledge service unavailable")


@router.get("/datasets")
async def list_datasets(
    page: int = 1,
    page_size: int = 20,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    dify: DifyClient = Depends(get_dify),
) -> dict:
    _require_dataset_key()
    result = await _call(dify.list_datasets(page=page, limit=page_size))
    # 租户过滤（契约 v8）：admin 全量；否则仅返回授权集合内的库（total 重算）
    visible = await resolve_visible_dataset_ids(db, user)
    if visible is not None:
        data = [d for d in result.get("data", []) if str(d.get("id", "")) in visible]
        result = {**result, "data": data, "total": len(data)}
    return result


@router.post("/datasets", status_code=status.HTTP_201_CREATED)
async def create_dataset(
    body: DatasetCreate,
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
    dify: DifyClient = Depends(get_dify),
) -> dict:
    """契约 v9：建空知识库（admin）；新库对创建者（admin）立即可见。"""
    _require_dataset_key()
    result = await _call(dify.create_dataset(body.name, body.indexing_technique))
    _audit(db, user, "dataset_create", result.get("id"), name=body.name)
    await db.commit()
    return result


@router.delete("/datasets/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dataset(
    dataset_id: str,
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
    dify: DifyClient = Depends(get_dify),
) -> None:
    """契约 v9：删库（admin）；本地授权行同步清理，防孤儿授权。"""
    _require_dataset_key()
    await _call(dify.delete_dataset(dataset_id))
    await db.execute(
        sa_delete(DatasetAuthorization).where(
            DatasetAuthorization.dataset_id == dataset_id
        )
    )
    _audit(db, user, "dataset_delete", dataset_id)
    await db.commit()


@router.get("/datasets/{dataset_id}/grants")
async def list_dataset_grants(
    dataset_id: str,
    _admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """契约 v9：库级授权视图（admin）——该库的三态授权全量清单。"""
    rows = (
        await db.execute(
            select(DatasetAuthorization).where(
                DatasetAuthorization.dataset_id == dataset_id
            )
        )
    ).scalars().all()
    items = []
    for r in rows:
        name = await _principal_name(db, r.principal_type, r.principal_id)
        items.append(
            {
                "principal_type": r.principal_type,
                "principal_id": r.principal_id,
                "name": name,  # 主体已被删除时为 null（行本身保留）
            }
        )
    items.sort(key=lambda x: (x["principal_type"], x["principal_id"]))
    return {"items": items}


@router.post(
    "/datasets/{dataset_id}/grants", status_code=status.HTTP_201_CREATED
)
async def add_dataset_grant(
    dataset_id: str,
    body: GrantCreate,
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """契约 v9：单条授权（幂等 upsert；主体不存在 → 404）。"""
    if await _principal_name(db, body.principal_type, body.principal_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "principal not found")
    exists = (
        await db.execute(
            select(DatasetAuthorization).where(
                DatasetAuthorization.dataset_id == dataset_id,
                DatasetAuthorization.principal_type == body.principal_type,
                DatasetAuthorization.principal_id == body.principal_id,
            )
        )
    ).scalar_one_or_none()
    if exists is None:
        db.add(
            DatasetAuthorization(
                dataset_id=dataset_id,
                principal_type=body.principal_type,
                principal_id=body.principal_id,
            )
        )
        _audit(
            db, user, "grant_add", dataset_id,
            principal_type=body.principal_type, principal_id=body.principal_id,
        )
        await db.commit()
    return {"principal_type": body.principal_type, "principal_id": body.principal_id}


@router.delete(
    "/datasets/{dataset_id}/grants/{principal_type}/{principal_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_dataset_grant(
    dataset_id: str,
    principal_type: str,
    principal_id: int,
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    """契约 v9：移除单条授权（幂等）。"""
    await db.execute(
        sa_delete(DatasetAuthorization).where(
            DatasetAuthorization.dataset_id == dataset_id,
            DatasetAuthorization.principal_type == principal_type,
            DatasetAuthorization.principal_id == principal_id,
        )
    )
    _audit(
        db, user, "grant_remove", dataset_id,
        principal_type=principal_type, principal_id=principal_id,
    )
    await db.commit()


@router.get("/audit")
async def list_kb_audit(
    page: int = 1,
    page_size: int = 20,
    _admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """契约 v9：审计流水（admin，新在前；含操作人姓名）。"""
    total = await db.scalar(select(func.count(KbAuditLog.id)))
    rows = (
        await db.execute(
            select(KbAuditLog, User.name)
            .outerjoin(User, User.id == KbAuditLog.user_id)
            .order_by(KbAuditLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return {
        "total": total or 0,
        "items": [
            {
                "id": log.id,
                "user": name,
                "action": log.action,
                "dataset_id": log.dataset_id,
                "detail": log.detail,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log, name in rows
        ],
    }


@router.get("/datasets/{dataset_id}/documents")
async def list_documents(
    dataset_id: str,
    page: int = 1,
    page_size: int = 20,
    keyword: str = "",
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    dify: DifyClient = Depends(get_dify),
) -> dict:
    _require_dataset_key()
    await _require_dataset_access(db, user, dataset_id)
    return await _call(
        dify.list_documents(dataset_id, page=page, limit=page_size, keyword=keyword or None)
    )


@router.post("/datasets/{dataset_id}/documents/text", status_code=status.HTTP_201_CREATED)
async def create_document_by_text(
    dataset_id: str,
    body: TextDocCreate,
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
    dify: DifyClient = Depends(get_dify),
) -> dict:
    _require_dataset_key()
    await _require_dataset_access(db, user, dataset_id)
    _logger.info("kb text doc create: user=%s dataset=%s name=%s", user.id, dataset_id, body.name)
    result = await _call(
        dify.create_doc_by_text(
            dataset_id, name=body.name, text=body.text,
            indexing_technique=body.indexing_technique,
        )
    )
    _audit(db, user, "doc_create_text", dataset_id, name=body.name)
    await db.commit()
    return result


@router.post(
    "/datasets/{dataset_id}/documents/file", status_code=status.HTTP_201_CREATED
)
async def create_document_by_file(
    dataset_id: str,
    request: Request,
    file: UploadFile = File(...),
    indexing_technique: str = Form("high_quality"),
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
    dify: DifyClient = Depends(get_dify),
) -> dict:
    _require_dataset_key()
    await _require_dataset_access(db, user, dataset_id)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES + 1024 * 1024:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "file too large")

    mime = (file.content_type or "").split(";")[0].strip().lower()
    if mime not in KB_DOC_MIMES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unsupported file type")

    content = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "file too large")
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty file")

    _logger.info(
        "kb file doc create: user=%s dataset=%s name=%s size=%d",
        user.id, dataset_id, file.filename, len(content),
    )
    result = await _call(
        dify.create_doc_by_file(
            dataset_id,
            filename=sanitize_filename(file.filename or "doc"),
            content=content,
            mime=mime,
            indexing_technique=indexing_technique,
        )
    )
    _audit(db, user, "doc_create_file", dataset_id, name=file.filename, size=len(content))
    await db.commit()
    return result


@router.delete(
    "/datasets/{dataset_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_document(
    dataset_id: str,
    document_id: str,
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
    dify: DifyClient = Depends(get_dify),
) -> None:
    _require_dataset_key()
    await _require_dataset_access(db, user, dataset_id)
    _logger.info("kb doc delete: user=%s dataset=%s doc=%s", user.id, dataset_id, document_id)
    await _call(dify.delete_document(dataset_id, document_id))
    _audit(db, user, "doc_delete", dataset_id, document_id=document_id)
    await db.commit()


@router.post("/datasets/{dataset_id}/retrieve")
async def retrieve(
    dataset_id: str,
    body: RetrieveQuery,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    dify: DifyClient = Depends(get_dify),
) -> dict:
    _require_dataset_key()
    await _require_dataset_access(db, user, dataset_id)
    return await _call(dify.retrieve(dataset_id, query=body.query))

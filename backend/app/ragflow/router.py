"""RAG 网关路由：RAGFlow 引擎代理（绞杀者路径，与 Dify kb 路由并存）。

权限立场与 kb 路由一致：读（列表/文档状态/检索）= 登录用户；写（建库/上传）
= PLATFORM_ADMIN。租户映射当前为单 key（RAGFLOW_API_KEY），多租户绑定表
随 onboarding 切片落地；届时此路由按登录用户解析 per-tenant key。
"""
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user, require_platform_admin
from app.core.config import settings
from app.db.session import get_db
from app.models.user import User
from app.ragflow.client import RagflowClient, RagflowError
from app.ragflow.deps import get_ragflow
from app.ragflow.parsing import route_for
from app.schemas.rag import RagDatasetCreate, RagRetrievalQuery

router = APIRouter(prefix="/api/rag", tags=["rag"])

_logger = logging.getLogger("app.ragflow")

MAX_RAG_UPLOAD_BYTES = 50 * 1024 * 1024  # 50MB/文件（评分表/审查单体量级）


def _require_ragflow(client: RagflowClient) -> None:
    if not client._api_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "rag engine not configured"
        )


def _map_upstream(e: RagflowError) -> HTTPException:
    # 越权/不存在 → 404（不泄露他租户资源存在性）；其余 → 502 上游错误
    if e.status_code in (401, 403) or "don't own" in e.message or "lacks permission" in e.message:
        return HTTPException(status.HTTP_404_NOT_FOUND, "dataset not found")
    return HTTPException(status.HTTP_502_BAD_GATEWAY, f"ragflow upstream: {e.message[:200]}")


@router.get("/datasets")
async def list_datasets(
    user: User = Depends(current_user),
    client: RagflowClient = Depends(get_ragflow),
) -> dict:
    _require_ragflow(client)
    try:
        return await client.list_datasets()
    except RagflowError as e:
        raise _map_upstream(e) from e


@router.post("/datasets", status_code=201)
async def create_dataset(
    payload: RagDatasetCreate,
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
    client: RagflowClient = Depends(get_ragflow),
) -> dict:
    _require_ragflow(client)
    try:
        data = await client.create_dataset(payload.name, payload.description)
    except RagflowError as e:
        raise _map_upstream(e) from e
    return {"id": data.get("id"), "name": payload.name}


@router.post("/datasets/{dataset_id}/documents", status_code=202)
async def upload_documents(
    dataset_id: str,
    files: list[UploadFile] = File(...),
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
    client: RagflowClient = Depends(get_ragflow),
) -> dict:
    """上传并立即触发解析（解析器由 parsing.route_for 按后缀路由）。"""
    _require_ragflow(client)
    if not files:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "no files")
    payload: list[tuple[str, bytes, str]] = []
    for f in files:
        try:
            strategy = route_for(f.filename or "")
        except ValueError as e:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(e)) from e
        content = await f.read()
        if len(content) > MAX_RAG_UPLOAD_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"{f.filename}: exceeds 50MB"
            )
        payload.append((f.filename or "doc", content, f.content_type or "application/octet-stream"))
        _logger.info("rag upload %s -> strategy=%s by user=%s", f.filename, strategy, user.id)
    try:
        docs = await client.upload_documents(dataset_id, payload)
        await client.trigger_parse(dataset_id, [d["id"] for d in docs])
    except RagflowError as e:
        raise _map_upstream(e) from e
    return {
        "accepted": [
            {"id": d.get("id"), "name": d.get("name"), "run": d.get("run")} for d in docs
        ]
    }


@router.get("/datasets/{dataset_id}/documents")
async def list_documents(
    dataset_id: str,
    user: User = Depends(current_user),
    client: RagflowClient = Depends(get_ragflow),
) -> dict:
    _require_ragflow(client)
    try:
        docs = await client.list_documents(dataset_id)
    except RagflowError as e:
        raise _map_upstream(e) from e
    return {
        "documents": [
            {"id": d.get("id"), "name": d.get("name"), "run": d.get("run"), "progress": d.get("progress")}
            for d in docs
        ]
    }


@router.post("/retrieval")
async def retrieval(
    payload: RagRetrievalQuery,
    user: User = Depends(current_user),
    client: RagflowClient = Depends(get_ragflow),
) -> dict:
    _require_ragflow(client)
    try:
        data = await client.retrieve(payload.question, payload.dataset_ids, payload.top_k)
    except RagflowError as e:
        raise _map_upstream(e) from e
    chunks = data.get("chunks") or []
    return {
        "chunks": [
            {
                "content": c.get("content") or c.get("content_with_weight"),
                "similarity": c.get("similarity"),
                "document_id": c.get("document_id"),
            }
            for c in chunks
        ]
    }

"""知识库路由（契约 v7）：Dify Knowledge API 的 JSON 代理。

权限：读（列表/文档/命中测试）= 所有登录用户；写（上传/删除）= PLATFORM_ADMIN。
审计：仅结构化日志，不落库（Dify 侧自带操作记录）。
边界：App↔知识库绑定只能在 Dify 控制台完成，Service API 无此能力。
"""
import logging

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user, require_platform_admin
from app.authz import is_dataset_authorized, resolve_visible_dataset_ids
from app.chat.service import _summarize_upstream_error
from app.db.session import get_db
from app.dify.client import DifyClient, DifyDatasetError, dataset_api_key
from app.dify.deps import get_dify
from app.files.router import MAX_UPLOAD_BYTES, sanitize_filename
from app.models.user import User
from app.schemas.kb import RetrieveQuery, TextDocCreate

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
    return await _call(
        dify.create_doc_by_text(
            dataset_id, name=body.name, text=body.text,
            indexing_technique=body.indexing_technique,
        )
    )


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
    return await _call(
        dify.create_doc_by_file(
            dataset_id,
            filename=sanitize_filename(file.filename or "doc"),
            content=content,
            mime=mime,
            indexing_technique=indexing_technique,
        )
    )


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

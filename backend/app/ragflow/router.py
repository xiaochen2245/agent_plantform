"""RAG 网关路由：RAGFlow 引擎代理（绞杀者路径，与 Dify kb 路由并存）。

权限立场与 kb 路由一致：读（列表/文档状态/检索）= 登录用户；写（建库/上传）
= PLATFORM_ADMIN。租户映射当前为单 key（RAGFLOW_API_KEY），多租户绑定表
随 onboarding 切片落地；届时此路由按登录用户解析 per-tenant key。
"""
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user, require_platform_admin
from app.core import vault
from app.db.session import get_db
from app.models.department import Department
from app.models.ragflow_binding import RagflowBinding
from app.models.user import User
from app.ragflow.client import RagflowClient, RagflowError
from app.ragflow.deps import get_ragflow
from app.ragflow.parsing import route_for
from app.ragflow.onboarding import ProvisionError, RagflowProvisioner
from app.ragflow.tagging import Tagger
from app.schemas.rag import (
    MetadataCondition,
    RagBindingCreate,
    RagDatasetCreate,
    RagRetrievalQuery,
)

router = APIRouter(prefix="/api/rag", tags=["rag"])

_logger = logging.getLogger("app.ragflow")

MAX_RAG_UPLOAD_BYTES = 50 * 1024 * 1024  # 50MB/文件（评分表/审查单体量级）


def _require_ragflow(client: RagflowClient) -> None:
    # 租户化后 client 一定带 key（无绑定时依赖层已 503）；保留给后备单租户 key 场景
    if not client._api_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "rag engine not configured"
        )


def _map_upstream(e: RagflowError) -> HTTPException:
    # 越权/不存在 → 404（不泄露他租户资源存在性）；其余 → 502 上游错误
    if e.status_code in (401, 403) or any(
        k in e.message for k in ("don't own", "lacks permission", "no authorization")
    ):
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
    user: User = Depends(current_user),
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
    user: User = Depends(current_user),
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
    extra: dict = {}
    if payload.metadata_condition:
        extra["metadata_condition"] = payload.metadata_condition.model_dump()
    try:
        data = await client.retrieve(
            payload.question, payload.dataset_ids, payload.top_k, **extra
        )
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


# ---- 功能④：打标（入库后的业务标签抽取） ----


@router.post("/datasets/{dataset_id}/documents/{document_id}/tag")
async def tag_document(
    dataset_id: str,
    document_id: str,
    user: User = Depends(current_user),
    client: RagflowClient = Depends(get_ragflow),
) -> dict:
    """拉取已解析文档 chunks → LLM 抽取标签 → 写回 RAGFlow metadata。
    解析未完成（无 chunks）→ 409；抽取失败 → 502（宁缺勿错，不写猜测标签）。"""
    _require_ragflow(client)
    try:
        chunks = await client.list_chunks(dataset_id, document_id)
    except RagflowError as e:
        raise _map_upstream(e) from e
    if not chunks:
        raise HTTPException(status.HTTP_409_CONFLICT, "document not parsed yet (no chunks)")
    labels = await Tagger().extract(chunks)
    if labels is None:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "label extraction failed")
    meta = Tagger.to_meta_fields(labels)
    try:
        await client.update_document_meta(dataset_id, document_id, meta)
    except RagflowError as e:
        raise _map_upstream(e) from e
    return {"document_id": document_id, "meta_fields": meta}


# ---- 问答（检索+LLM+引用，RAGFlow Chat Assistant） ----


class RagChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant|system)$")
    content: str = Field(min_length=1, max_length=8192)


class RagChatBody(BaseModel):
    messages: list[RagChatMessage] = Field(min_length=1, max_length=40)


@router.get("/chat/assistant")
async def ensure_chat_assistant(
    user: User = Depends(current_user),
    client: RagflowClient = Depends(get_ragflow),
) -> dict:
    """确保本租户存在 portal-assistant（绑定默认库），返回 chat_id。"""
    _require_ragflow(client)
    try:
        ds = await client.list_datasets()
        ids = [d["id"] for d in (ds.get("data") or [])] or []
        chat_id = await client.ensure_chat(ids[:1])
    except RagflowError as e:
        raise _map_upstream(e) from e
    return {"chat_id": chat_id}


@router.post("/chat/completions")
async def chat_completions(
    payload: RagChatBody,
    user: User = Depends(current_user),
    client: RagflowClient = Depends(get_ragflow),
):
    """部门知识库问答（SSE 流式，OpenAI 兼容透传，含引用）。"""
    _require_ragflow(client)
    try:
        ds = await client.list_datasets()
        ids = [d["id"] for d in (ds.get("data") or [])] or []
        chat_id = await client.ensure_chat(ids[:1])
        stream = await client.stream_chat(chat_id, [m.model_dump() for m in payload.messages])
    except RagflowError as e:
        raise _map_upstream(e) from e
    return StreamingResponse(stream, media_type="text/event-stream")


# ---- 知识库 CRUD ----


@router.delete("/datasets/{dataset_id}", status_code=204)
async def delete_dataset(
    dataset_id: str,
    user: User = Depends(current_user),
    client: RagflowClient = Depends(get_ragflow),
) -> None:
    _require_ragflow(client)
    try:
        await client.delete_dataset(dataset_id)
    except RagflowError as e:
        raise _map_upstream(e) from e


class RagDatasetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)


@router.patch("/datasets/{dataset_id}")
async def update_dataset(
    dataset_id: str,
    payload: RagDatasetUpdate,
    user: User = Depends(current_user),
    client: RagflowClient = Depends(get_ragflow),
) -> dict:
    _require_ragflow(client)
    try:
        await client.update_dataset(dataset_id, payload.name, payload.description)
    except RagflowError as e:
        raise _map_upstream(e) from e
    return {"id": dataset_id}


class RagDocDelete(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100)


@router.delete("/datasets/{dataset_id}/documents", status_code=204)
async def delete_documents(
    dataset_id: str,
    payload: RagDocDelete,
    user: User = Depends(current_user),
    client: RagflowClient = Depends(get_ragflow),
) -> None:
    _require_ragflow(client)
    try:
        await client.delete_documents(dataset_id, payload.ids)
    except RagflowError as e:
        raise _map_upstream(e) from e


# ---- 租户绑定管理（PLATFORM_ADMIN） ----


@router.get("/bindings", dependencies=[Depends(require_platform_admin)])
async def list_bindings(
    db: AsyncSession = Depends(get_db),
) -> dict:
    rows = (await db.scalars(select(RagflowBinding))).all()
    return {
        "bindings": [
            {
                "id": b.id,
                "department_id": b.department_id,
                "ragflow_email": b.ragflow_email,
                "default_dataset_id": b.default_dataset_id,
                "status": b.status,
            }
            for b in rows
        ]
    }


@router.post("/bindings", status_code=201)
async def create_binding(
    payload: RagBindingCreate,
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """全自动开通：注册影子账号→发 key→绑模型→建默认库（见 onboarding.py）。"""
    dept = await db.get(Department, payload.department_id)
    if dept is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "department not found")
    exists = await db.scalar(
        select(RagflowBinding).where(RagflowBinding.department_id == payload.department_id)
    )
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "binding already exists")
    email = f"{payload.email_prefix or f'dept-{payload.department_id}'}@ragflow.local"
    provisioner = RagflowProvisioner()
    try:
        api_token, dataset_id, password = await provisioner.provision(email)
    except ProvisionError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"ragflow provisioning failed: {e}")
    finally:
        await provisioner.aclose()
    binding = RagflowBinding(
        department_id=payload.department_id,
        ragflow_email=email,
        ragflow_password_enc=vault.encrypt(password),
        ragflow_api_token_enc=vault.encrypt(api_token),
        default_dataset_id=dataset_id,
    )
    db.add(binding)
    await db.commit()
    _logger.info(
        "rag binding created dept=%s email=%s by user=%s",
        payload.department_id, email, user.id,
    )
    return {"department_id": payload.department_id, "default_dataset_id": dataset_id}

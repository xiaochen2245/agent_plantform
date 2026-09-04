"""RAG 网关路由：RAGFlow 引擎代理（绞杀者路径，与 Dify kb 路由并存）。

权限立场与 kb 路由一致：读（列表/文档状态/检索）= 登录用户；写（建库/上传）
= PLATFORM_ADMIN。租户映射当前为单 key（RAGFLOW_API_KEY），多租户绑定表
随 onboarding 切片落地；届时此路由按登录用户解析 per-tenant key。
"""
import json
import logging
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user, require_platform_admin
from app.core import vault
from app.db.session import get_db
from app.models.department import Department
from app.models.app import App
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.rag_audit import RagAuditLog
from app.models.ragflow_binding import RagflowBinding
from app.models.user import User
from app.ragflow.autotag import spawn_autotag
from app.ragflow.client import RagflowClient, RagflowError
from app.ragflow.deps import get_ragflow
from app.ragflow.parsing import route_for
from app.ragflow.policy import visible_document_ids
from app.ragflow.onboarding import ProvisionError, RagflowProvisioner
from app.ragflow.tagging import Tagger
from app.schemas.rag import (
    MetadataCondition,
    RagBindingCreate,
    RagChunkUpdate,
    RagDatasetCreate,
    RagRetrievalQuery,
    RagSessionCreate,
    RagSessionSync,
)

router = APIRouter(prefix="/api/rag", tags=["rag"])

_logger = logging.getLogger("app.ragflow")

MAX_RAG_UPLOAD_BYTES = 50 * 1024 * 1024  # 50MB/文件（评分表/审查单体量级）


def _audit(
    db: AsyncSession, user: User, action: str, dataset_id: str | None = None, **detail
) -> None:
    """写操作审计（#28）：随请求事务提交（kb 路由契约 v9 同模式）。"""
    db.add(
        RagAuditLog(
            user_id=user.id,
            action=action,
            dataset_id=dataset_id,
            detail=json.dumps(detail, ensure_ascii=False) if detail else None,
        )
    )


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


def _retrieval_chunk(c: dict) -> dict:
    """检索结果全字段透传（P0-①：引用溯源需文档名/得分/位置）。"""
    return {
        "id": c.get("id"),
        "content": c.get("content") or c.get("content_with_weight"),
        "document_id": c.get("document_id"),
        "document_keyword": c.get("document_keyword"),
        "dataset_id": c.get("dataset_id"),
        "similarity": c.get("similarity"),
        "term_similarity": c.get("term_similarity"),
        "vector_similarity": c.get("vector_similarity"),
        "positions": c.get("positions"),
        "highlight": c.get("highlight"),
    }


def _chunk_brief(c: dict) -> dict:
    """切片列表/单条网关形状（P0-③：切片查看与纠错）。"""
    return {
        "id": c.get("id"),
        # 引擎 chunk 列表/单条的字段名是 content_with_weight（SSE 引用路径同此回退）
        "content": c.get("content") or c.get("content_with_weight"),
        "document_id": c.get("document_id"),
        "available": c.get("available"),
        "important_keywords": c.get("important_keywords"),
        "positions": c.get("positions"),
    }


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
    _audit(db, user, "dataset.create", data.get("id"), name=payload.name)
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
    # 解析为异步：后台轮询 run=DONE 自动打标（失败仅记日志，按钮兜底重试）
    spawn_autotag(client, dataset_id, [d["id"] for d in docs], user_id=user.id)
    _audit(db, user, "doc.upload", dataset_id, files=[d.get("name") for d in docs])
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
    db: AsyncSession = Depends(get_db),
    client: RagflowClient = Depends(get_ragflow),
) -> dict:
    """检索（#29）：document_ids 白名单由服务端推导，客户端不可传。
    P0-②：检索台参数化——阈值/向量权重/重排/关键词/高亮透传，top_n 为
    网关自有截断（映射引擎 page_size，不透传已弃用的 top_k）。
    当前策略方案 A（部门内全员可见）→ 不过滤；owner 拍细粒度后仅改
    visible_document_ids 实现，通道与路由不变。"""
    _require_ragflow(client)
    doc_ids = await visible_document_ids(db, user, payload.dataset_ids)
    extra: dict = {}
    if payload.metadata_condition:
        extra["metadata_condition"] = payload.metadata_condition.model_dump()
    try:
        data = await client.retrieve(
            payload.question, payload.dataset_ids,
            page_size=payload.top_n,
            similarity_threshold=payload.similarity_threshold,
            vector_similarity_weight=payload.vector_similarity_weight,
            rerank_id=payload.rerank_id,
            keyword=payload.keyword,
            highlight=payload.highlight,
            document_ids=doc_ids, **extra,
        )
    except RagflowError as e:
        raise _map_upstream(e) from e
    return {"chunks": [_retrieval_chunk(c) for c in data.get("chunks") or []]}


# ---- 功能④：打标（入库后的业务标签抽取） ----


@router.post("/datasets/{dataset_id}/documents/{document_id}/tag")
async def tag_document(
    dataset_id: str,
    document_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
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
    _audit(db, user, "doc.tag", dataset_id, document_id=document_id, source="manual")
    return {"document_id": document_id, "meta_fields": meta}


# ---- P0-③：切片通道（查看全员 / 纠错 ADMIN）与重解析 ----


@router.get("/datasets/{dataset_id}/documents/{document_id}/chunks")
async def list_chunks(
    dataset_id: str,
    document_id: str,
    keywords: str = Query(default="", max_length=128),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(current_user),
    client: RagflowClient = Depends(get_ragflow),
) -> dict:
    _require_ragflow(client)
    try:
        data = await client.list_chunks_page(
            dataset_id, document_id, keywords=keywords, page=page, page_size=page_size
        )
    except RagflowError as e:
        raise _map_upstream(e) from e
    return {
        "chunks": [_chunk_brief(c) for c in data.get("chunks") or []],
        "total": data.get("total"),
    }


@router.get("/datasets/{dataset_id}/documents/{document_id}/chunks/{chunk_id}")
async def get_chunk(
    dataset_id: str,
    document_id: str,
    chunk_id: str,
    user: User = Depends(current_user),
    client: RagflowClient = Depends(get_ragflow),
) -> dict:
    _require_ragflow(client)
    try:
        c = await client.get_chunk(dataset_id, document_id, chunk_id)
    except RagflowError as e:
        raise _map_upstream(e) from e
    return _chunk_brief(c)


@router.patch("/datasets/{dataset_id}/documents/{document_id}/chunks/{chunk_id}")
async def update_chunk(
    dataset_id: str,
    document_id: str,
    chunk_id: str,
    payload: RagChunkUpdate,
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
    client: RagflowClient = Depends(get_ragflow),
) -> dict:
    """切片手动纠错（P0-③）：写口径与库管理一致仅 PLATFORM_ADMIN，入审计。"""
    fields = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "empty chunk update")
    try:
        await client.update_chunk(dataset_id, document_id, chunk_id, fields)
    except RagflowError as e:
        raise _map_upstream(e) from e
    _audit(
        db, user, "chunk.update", dataset_id,
        document_id=document_id, chunk_id=chunk_id, fields=sorted(fields),
    )
    return {"id": chunk_id, "updated": sorted(fields)}


@router.delete("/datasets/{dataset_id}/documents/{document_id}/chunks/{chunk_id}", status_code=204)
async def delete_chunk(
    dataset_id: str,
    document_id: str,
    chunk_id: str,
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
    client: RagflowClient = Depends(get_ragflow),
) -> None:
    try:
        await client.delete_chunks(dataset_id, document_id, [chunk_id])
    except RagflowError as e:
        raise _map_upstream(e) from e
    _audit(db, user, "chunk.delete", dataset_id, document_id=document_id, chunk_id=chunk_id)


@router.post("/datasets/{dataset_id}/documents/{document_id}/parse", status_code=202)
async def parse_document(
    dataset_id: str,
    document_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    client: RagflowClient = Depends(get_ragflow),
) -> dict:
    """重试解析（P0-③：FAIL 原因可见后的一键重解析；口径对齐上传=登录用户）。"""
    _require_ragflow(client)
    try:
        await client.trigger_parse(dataset_id, [document_id])
    except RagflowError as e:
        raise _map_upstream(e) from e
    _audit(db, user, "doc.parse", dataset_id, document_id=document_id)
    return {"document_id": document_id, "accepted": True}


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
    """确保本租户存在 portal-assistant（绑定全量库），返回 chat_id。"""
    _require_ragflow(client)
    try:
        ds = await client.list_datasets()
        ids = [d["id"] for d in (ds.get("data") or [])] or []
        chat_id = await client.ensure_chat(ids)
    except RagflowError as e:
        raise _map_upstream(e) from e
    return {"chat_id": chat_id}


@router.post("/chat/completions")
async def chat_completions(
    payload: RagChatBody,
    user: User = Depends(current_user),
    client: RagflowClient = Depends(get_ragflow),
):
    """部门知识库问答（SSE 流式，OpenAI 兼容透传，含引用）。
    P0-0：assistant 绑定全量租户库（原 ids[:1] → 多库检索不全）。"""
    _require_ragflow(client)
    try:
        ds = await client.list_datasets()
        ids = [d["id"] for d in (ds.get("data") or [])] or []
        chat_id = await client.ensure_chat(ids)
        stream = await client.stream_chat(chat_id, [m.model_dump() for m in payload.messages])
    except RagflowError as e:
        raise _map_upstream(e) from e
    return StreamingResponse(stream, media_type="text/event-stream")


# ---- 知识库 CRUD ----


@router.delete("/datasets/{dataset_id}", status_code=204)
async def delete_dataset(
    dataset_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    client: RagflowClient = Depends(get_ragflow),
) -> None:
    _require_ragflow(client)
    try:
        await client.delete_dataset(dataset_id)
    except RagflowError as e:
        raise _map_upstream(e) from e
    _audit(db, user, "dataset.delete", dataset_id)


class RagDatasetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)


@router.patch("/datasets/{dataset_id}")
async def update_dataset(
    dataset_id: str,
    payload: RagDatasetUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    client: RagflowClient = Depends(get_ragflow),
) -> dict:
    _require_ragflow(client)
    try:
        await client.update_dataset(dataset_id, payload.name, payload.description)
    except RagflowError as e:
        raise _map_upstream(e) from e
    _audit(
        db, user, "dataset.update", dataset_id,
        **{"name": payload.name, "description": payload.description},
    )
    return {"id": dataset_id}


class RagDocDelete(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100)


@router.delete("/datasets/{dataset_id}/documents", status_code=204)
async def delete_documents(
    dataset_id: str,
    payload: RagDocDelete,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    client: RagflowClient = Depends(get_ragflow),
) -> None:
    _require_ragflow(client)
    try:
        await client.delete_documents(dataset_id, payload.ids)
    except RagflowError as e:
        raise _map_upstream(e) from e
    _audit(db, user, "doc.delete", dataset_id, ids=payload.ids)


# ---- 审计查询（#28：按用户+时间追溯） ----


@router.get("/audit", dependencies=[Depends(require_platform_admin)])
async def list_audit(
    user_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    q = (
        select(RagAuditLog, User.name)
        .outerjoin(User, User.id == RagAuditLog.user_id)
        .order_by(RagAuditLog.id.desc())
        .limit(limit)
    )
    if user_id is not None:
        q = q.where(RagAuditLog.user_id == user_id)
    rows = (await db.execute(q)).all()
    return {
        "logs": [
            {
                "user_id": log.user_id,
                "user_name": name,
                "action": log.action,
                "dataset_id": log.dataset_id,
                "detail": log.detail,
                "created_at": log.created_at.isoformat(),
            }
            for log, name in rows
        ]
    }


# ---- 会话持久化（#38：ChatSurface 多轮不丢，审查/比对应用同接口复用） ----


@router.post("/chat/sessions", status_code=201)
async def create_chat_session(
    payload: RagSessionCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """新建会话（复用 Conversation 表；app_id 关联门户应用，缺省知识库 app=1）。"""
    app = await db.get(App, payload.app_id)
    if app is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app not found")
    conv = Conversation(user_id=user.id, app_id=payload.app_id, title=payload.title or "")
    db.add(conv)
    await db.commit()
    return {"id": str(conv.id), "title": conv.title}


@router.get("/chat/sessions")
async def list_chat_sessions(
    app_id: int = 1,
    limit: int = 20,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    q = (
        select(Conversation)
        .where(
            Conversation.user_id == user.id,
            Conversation.deleted_at.is_(None),
            Conversation.app_id == app_id,
        )
        .order_by(Conversation.updated_at.desc())
        .limit(max(1, min(limit, 100)))
    )
    rows = (await db.scalars(q)).all()
    return {
        "sessions": [
            {
                "id": str(r.id),
                "title": r.title or "新会话",
                "message_count": r.message_count or 0,
                "updated_at": r.updated_at.isoformat(),
            }
            for r in rows
        ]
    }


async def _own_session(db: AsyncSession, user: User, session_id: str) -> Conversation:
    """仅本人会话；不足 uuid/不存在/他人 → 404（不泄露存在性）。"""
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    conv = await db.get(Conversation, sid)
    if conv is None or conv.user_id != user.id or conv.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    return conv


@router.get("/chat/sessions/{session_id}/messages")
async def chat_session_messages(
    session_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    conv = await _own_session(db, user, session_id)
    rows = (
        await db.scalars(
            select(Message)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.id.asc())
        )
    ).all()
    return {
        "messages": [
            {"role": r.role, "content": r.content, "created_at": r.created_at.isoformat()}
            for r in rows
        ]
    }


@router.put("/chat/sessions/{session_id}/messages")
async def sync_chat_session(
    session_id: str,
    payload: RagSessionSync,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """全量同步会话轮次（客户端完成一轮后上报完整列表，幂等重写）。
    ponytail: 信任客户端内容的全量重写——P1 内部门户够用；对外产品需服务端
    拦流落库（chat_completions 流式 tee）再升级。"""
    conv = await _own_session(db, user, session_id)
    await db.execute(
        sa_delete(Message).where(Message.conversation_id == conv.id)
    )
    for m in payload.messages:
        db.add(Message(conversation_id=conv.id, role=m.role, content=m.content))
    conv.message_count = len(payload.messages)
    if payload.title is not None:
        conv.title = payload.title[:200]
    await db.commit()
    return {"id": str(conv.id), "message_count": conv.message_count}


@router.delete("/chat/sessions/{session_id}", status_code=204)
async def delete_chat_session(
    session_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    conv = await _own_session(db, user, session_id)
    conv.deleted_at = func.now()
    await db.commit()


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
    _audit(db, user, "binding.create", dataset_id, department_id=payload.department_id, email=email)
    _logger.info(
        "rag binding created dept=%s email=%s by user=%s",
        payload.department_id, email, user.id,
    )
    return {"department_id": payload.department_id, "default_dataset_id": dataset_id}

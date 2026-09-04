"""
文档接入与解析状态 API 路由 (Documents API)
提供:
1. POST /api/v1/documents/upload: 多源异构工程文档上传并触发异步解析
2. GET /api/v1/documents/{doc_id}/status: 查询指定文档解析状态、AST 节点与切片统计
3. GET /api/v1/documents: 分页查询当前租户文档列表
"""

import hashlib
import os
from pathlib import Path
from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_tenant_db, get_tenant_id
from app.celery_app import parse_document_task
from app.core.config import settings
from app.models.audit_rag import Document, DocumentChunk, Tenant
from app.schemas.gateway import (
    DocumentListResponse,
    DocumentStatusResponse,
    DocumentUploadResponse,
)

router = APIRouter()

SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".xlsx", ".mpp", ".cad", ".dxf", ".ofd", ".pptx", ".txt"
}


async def _ensure_tenant(session: AsyncSession, tenant_id: str) -> None:
    """确保租户根记录存在，若不存在则安全初始化"""
    tenant = await session.get(Tenant, tenant_id)
    if not tenant:
        tenant = Tenant(
            id=tenant_id,
            code=tenant_id,
            name=f"Enterprise {tenant_id}",
        )
        session.add(tenant)
        await session.flush()


@router.post("/upload", response_model=DocumentUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
) -> DocumentUploadResponse:
    """
    上传工程文档并自动投递至 Celery 异步长任务队列执行解析与切片。
    """
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="文件名不能为空")

    _, ext = os.path.splitext(file.filename)
    norm_ext = ext.lower()
    if norm_ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的文档格式: '{ext}'。仅支持 {sorted(list(SUPPORTED_EXTENSIONS))}",
        )

    content = await file.read()
    if not content or len(content.strip()) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文档内容为空 (0 字节)，无法解析",
        )

    file_hash = hashlib.sha256(content).hexdigest()
    doc_id = f"doc_{uuid.uuid4().hex[:12]}"

    # 本地文件持久化落盘
    save_dir = Path(settings.UPLOAD_DIR) / tenant_id / file_hash
    save_dir.mkdir(parents=True, exist_ok=True)
    file_path = save_dir / file.filename
    file_path.write_bytes(content)

    # 确保租户记录存在
    await _ensure_tenant(db, tenant_id)

    # 创建 Document 实体
    doc = Document(
        id=doc_id,
        tenant_id=tenant_id,
        title=file.filename,
        file_type=norm_ext.lstrip("."),
        s3_path=str(file_path),
        file_hash=file_hash,
        parse_status="pending",
        doc_ast={},
        doc_metadata={"file_size_bytes": len(content), "original_name": file.filename},
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    # 派发异步解析 Celery 任务
    async_task = parse_document_task.delay(
        document_id=doc.id,
        tenant_id=tenant_id,
        file_path=str(file_path),
        file_name=file.filename,
    )
    task_id = getattr(async_task, "id", f"task_{uuid.uuid4().hex[:8]}")

    # 重新刷新状态 (若为 eager 同步模式，任务已在此刻完成)
    await db.refresh(doc)

    return DocumentUploadResponse(
        document_id=doc.id,
        task_id=str(task_id),
        file_name=doc.title,
        file_type=doc.file_type,
        file_size_bytes=len(content),
        parse_status=doc.parse_status,
        created_at=doc.created_at,
    )


@router.get("/{doc_id}/status", response_model=DocumentStatusResponse)
async def get_document_status(
    doc_id: str,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
) -> DocumentStatusResponse:
    """
    查询文档解析状态、AST 节点与父子切片统计。
    在多租户 RLS 与应用层租户硬隔离下，只能查看本租户拥有的文档。
    """
    stmt = select(Document).where(Document.id == doc_id, Document.tenant_id == tenant_id)
    res = await db.execute(stmt)
    doc = res.scalar_one_or_none()
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"文档未找到: id={doc_id}",
        )

    # 统计切片数量
    chunk_count_stmt = select(func.count(DocumentChunk.id)).where(
        DocumentChunk.document_id == doc_id,
        DocumentChunk.tenant_id == tenant_id,
    )
    chunk_count = (await db.execute(chunk_count_stmt)).scalar() or 0

    ast_node_count = 0
    if doc.doc_ast and isinstance(doc.doc_ast, dict):
        ast_node_count = len(doc.doc_ast.get("nodes", []))

    error_msg = None
    if doc.doc_metadata and isinstance(doc.doc_metadata, dict):
        error_msg = doc.doc_metadata.get("error_message")

    return DocumentStatusResponse(
        document_id=doc.id,
        tenant_id=doc.tenant_id,
        file_name=doc.title,
        file_type=doc.file_type,
        file_size_bytes=doc.doc_metadata.get("file_size_bytes", 0) if doc.doc_metadata else 0,
        parse_status=doc.parse_status,
        total_chunks=chunk_count,
        ast_node_count=ast_node_count,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
        error_message=error_msg,
    )


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> DocumentListResponse:
    """
    分页获取当前租户下的所有文档列表。
    """
    count_stmt = select(func.count(Document.id)).where(Document.tenant_id == tenant_id)
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = (
        select(Document)
        .where(Document.tenant_id == tenant_id)
        .order_by(Document.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    docs = (await db.execute(stmt)).scalars().all()

    items: List[DocumentStatusResponse] = []
    for doc in docs:
        ast_node_count = len(doc.doc_ast.get("nodes", [])) if doc.doc_ast and isinstance(doc.doc_ast, dict) else 0
        items.append(
            DocumentStatusResponse(
                document_id=doc.id,
                tenant_id=doc.tenant_id,
                file_name=doc.title,
                file_type=doc.file_type,
                file_size_bytes=doc.doc_metadata.get("file_size_bytes", 0) if doc.doc_metadata else 0,
                parse_status=doc.parse_status,
                total_chunks=0,
                ast_node_count=ast_node_count,
                created_at=doc.created_at,
                updated_at=doc.updated_at,
                error_message=doc.doc_metadata.get("error_message") if doc.doc_metadata else None,
            )
        )

    return DocumentListResponse(total=total, items=items)

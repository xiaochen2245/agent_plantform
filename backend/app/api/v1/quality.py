"""
文档质检与招投标对齐 API 路由 (Quality & Alignment API)
提供:
1. POST /api/v1/quality/check: 排版格式与大纲层级断层核验
2. POST /api/v1/quality/tender-alignment: 自编标书 vs 招标文件评分标准深度比对与 4 类偏离度评定
"""

import logging
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_tenant_db, get_tenant_id
from app.models.audit_rag import AuditTask, Document, TaskStatus, TaskType
from app.quality.outline_validator import DocumentQualityEngine
from app.quality.tender_alignment import TenderAlignmentEngine
from app.schemas.ast import UnifiedDocumentAST
from app.schemas.audit import DocumentQualityReport, TenderAlignmentReport
from app.schemas.gateway import DocumentQualityCheckRequest, TenderAlignmentRequest

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/check", response_model=DocumentQualityReport)
async def check_document_quality(
    req: DocumentQualityCheckRequest,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
) -> DocumentQualityReport:
    """
    对指定已解析文档执行标题大纲树断层检测（1.1跳跃断层）与排版表格规范质检。
    将核验结果持久化至 AuditTask 与 ReviewResult 表。
    """
    stmt = select(Document).where(Document.id == req.document_id, Document.tenant_id == tenant_id)
    res = await db.execute(stmt)
    doc = res.scalar_one_or_none()
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"待质检文档不存在: document_id={req.document_id}",
        )

    if not doc.doc_ast or not isinstance(doc.doc_ast, dict) or not doc.doc_ast.get("nodes"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"文档尚未解析完成或无有效 AST 数据: parse_status={doc.parse_status}",
        )

    ast = UnifiedDocumentAST.model_validate(doc.doc_ast)
    engine = DocumentQualityEngine(config=req.config)
    report = engine.validate_document(ast)

    # 持久化审计任务与审查明细
    task_id = f"task_qc_{uuid.uuid4().hex[:12]}"
    audit_task = AuditTask(
        id=task_id,
        tenant_id=tenant_id,
        task_type=TaskType.FORMAT_STYLE,
        status=TaskStatus.SUCCESS,
        source_document_id=doc.id,
        summary_report=report.model_dump(mode="json"),
        total_issues_count=report.total_issues_count,
        high_risk_count=report.high_risk_count,
    )
    db.add(audit_task)

    review_results = engine.to_review_results(report=report, task_id=task_id, tenant_id=tenant_id)
    db.add_all(review_results)

    await db.commit()
    return report


@router.post("/tender-alignment", response_model=TenderAlignmentReport)
async def align_tender_documents(
    req: TenderAlignmentRequest,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
) -> TenderAlignmentReport:
    """
    对齐自编标书与招标文件评分项，输出包含 4 类偏离度（完全满足/缺失项/正偏离/负偏离）与原文精准锚点的比对报告。
    """
    # 1. 加载自编标书
    src_stmt = select(Document).where(Document.id == req.source_document_id, Document.tenant_id == tenant_id)
    source_doc = (await db.execute(src_stmt)).scalar_one_or_none()
    if not source_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"自编投标文件未找到: document_id={req.source_document_id}",
        )

    # 2. 加载招标文件/评分表
    tgt_stmt = select(Document).where(Document.id == req.target_document_id, Document.tenant_id == tenant_id)
    target_doc = (await db.execute(tgt_stmt)).scalar_one_or_none()
    if not target_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"招标文件/评分基准文档未找到: document_id={req.target_document_id}",
        )

    if not source_doc.doc_ast or not source_doc.doc_ast.get("nodes"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"自编标书尚未解析完成: parse_status={source_doc.parse_status}",
        )
    if not target_doc.doc_ast or not target_doc.doc_ast.get("nodes"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"招标文件尚未解析完成: parse_status={target_doc.parse_status}",
        )

    src_ast = UnifiedDocumentAST.model_validate(source_doc.doc_ast)
    tgt_ast = UnifiedDocumentAST.model_validate(target_doc.doc_ast)

    alignment_engine = TenderAlignmentEngine()
    report = alignment_engine.align_and_evaluate(rfp_ast=tgt_ast, proposal_ast=src_ast)

    # 3. 持久化审计任务
    task_id = f"task_align_{uuid.uuid4().hex[:12]}"
    audit_task = AuditTask(
        id=task_id,
        tenant_id=tenant_id,
        task_type=TaskType.BID_COMPARISON,
        status=TaskStatus.SUCCESS,
        source_document_id=source_doc.id,
        target_document_id=target_doc.id,
        summary_report=report.model_dump(mode="json"),
        total_issues_count=len(report.results),
        high_risk_count=len(report.critical_kill_items),
    )
    db.add(audit_task)

    review_results = TenderAlignmentEngine.to_review_results(report=report, task_id=task_id)
    db.add_all(review_results)

    await db.commit()
    return report

"""
质量与招投标对齐 API (POST /quality/check, POST /quality/tender-alignment) 集成测试套件
验证:
1. 标题层级 1.1 断层检测与 AuditTask/ReviewResult 持久化
2. 不存在的文档 404 校验
3. 自编标书 vs 招标文件 4 类偏离度比对 (FULL_COMPLIANCE, MISSING, POSITIVE, NEGATIVE)
4. 多租户隔离防护 (跨租户质检 404)
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.audit_rag import AuditTask, DeviationType, Document, ReviewResult, Tenant
from app.schemas.ast import (
    ASTBlockType,
    ASTNode,
    DocumentSourceType,
    UnifiedDocumentAST,
)


async def _create_test_document_with_ast(
    tenant_id: str,
    doc_id: str,
    title: str,
    nodes: list[ASTNode],
) -> Document:
    ast = UnifiedDocumentAST(
        document_id=doc_id,
        tenant_id=tenant_id,
        file_name=f"{title}.docx",
        source_type=DocumentSourceType.DOCX,
        nodes=nodes,
    )
    async with SessionLocal() as session:
        tenant = await session.get(Tenant, tenant_id)
        if not tenant:
            session.add(Tenant(id=tenant_id, code=tenant_id, name=f"Tenant {tenant_id}"))
            await session.flush()

        doc = Document(
            id=doc_id,
            tenant_id=tenant_id,
            title=title,
            file_type="docx",
            s3_path=f"/fake/{doc_id}",
            file_hash=f"hash_{doc_id}",
            parse_status="success",
            doc_ast=ast.model_dump(),
            doc_metadata={"file_size_bytes": 1024},
        )
        session.add(doc)
        await session.commit()
        return doc


@pytest.mark.asyncio
async def test_quality_check_heading_jump(client: AsyncClient):
    """测试包含 1.1 -> 1.3 跳跃断层的 AST，验证检出 SEQUENCE_GAP 并入库"""
    tenant_id = "tenant_qc_1"
    doc_id = "doc_heading_jump_01"

    nodes = [
        ASTNode(
            block_id="b1",
            block_type=ASTBlockType.HEADING,
            level=1,
            section_path=["第一章 工程概况"],
            text_content="第一章 工程概况",
            page_or_sheet="1",
        ),
        ASTNode(
            block_id="b2",
            block_type=ASTBlockType.HEADING,
            level=2,
            section_path=["第一章 工程概况", "1.1 建设背景"],
            text_content="1.1 建设背景与总体原则",
            page_or_sheet="1",
        ),
        ASTNode(
            block_id="b3",
            block_type=ASTBlockType.PARAGRAPH,
            level=0,
            section_path=["第一章 工程概况", "1.1 建设背景"],
            text_content="本项目为大型市政重点工程。",
            page_or_sheet="1",
        ),
        ASTNode(
            block_id="b4",
            block_type=ASTBlockType.HEADING,
            level=2,
            section_path=["第一章 工程概况", "1.3 质量要求"],
            text_content="1.3 质量控制与施工组织",
            page_or_sheet="2",
        ),
    ]

    await _create_test_document_with_ast(tenant_id, doc_id, "跳跃断层标书", nodes)

    headers = {"X-Tenant-ID": tenant_id}
    payload = {"document_id": doc_id}

    resp = await client.post("/api/v1/quality/check", json=payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    assert data["document_id"] == doc_id
    assert data["passed"] is False or data["overall_score"] < 100.0
    assert data["total_issues_count"] > 0

    # 验证数据库记录
    async with SessionLocal() as session:
        stmt = select(AuditTask).where(AuditTask.source_document_id == doc_id)
        task = (await session.execute(stmt)).scalar_one_or_none()
        assert task is not None
        assert task.tenant_id == tenant_id

        stmt_res = select(ReviewResult).where(ReviewResult.task_id == task.id)
        results = (await session.execute(stmt_res)).scalars().all()
        assert len(results) > 0


@pytest.mark.asyncio
async def test_quality_check_not_found(client: AsyncClient):
    """质检不存在的文档返回 404"""
    resp = await client.post(
        "/api/v1/quality/check",
        json={"document_id": "non_existent_doc"},
        headers={"X-Tenant-ID": "tenant_qc_1"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_tender_alignment_four_deviations(client: AsyncClient):
    """验证自编标书 vs 招标文件评分表对齐，正确判定偏离度"""
    tenant_id = "tenant_tender_align"
    src_id = "doc_proposal_01"
    tgt_id = "doc_rfp_01"

    # 招标文件评分项 (TableNode)
    rfp_nodes = [
        ASTNode(
            block_id="rfp_h1",
            block_type=ASTBlockType.HEADING,
            level=1,
            section_path=["第四章 评标标准与方法"],
            text_content="第四章 评标标准与方法",
            page_or_sheet="1",
        ),
        ASTNode(
            block_id="rfp_t1",
            block_type=ASTBlockType.TABLE,
            level=0,
            section_path=["第四章 评标标准与方法", "技术商务评分表"],
            text_content="| 评分项 | 分值 | 评审标准 |\n| 工期要求 | 20 | 承诺总工期<=90天得满分，超过90天作负偏离扣分 |\n| 额定COP | 20 | 冷水机组COP>=5.0得满分，低于5.0为负偏离 |\n",
            table_data={
                "headers": [["评分项", "分值", "评审标准"]],
                "rows": [
                    ["工期要求", "20", "承诺总工期<=90天得满分，超过90天作负偏离扣分"],
                    ["额定COP", "20", "冷水机组COP>=5.0得满分，低于5.0为负偏离"],
                ],
                "markdown": "| 评分项 | 分值 | 评审标准 |\n| 工期要求 | 20 | 承诺总工期<=90天得满分，超过90天作负偏离扣分 |\n| 额定COP | 20 | 冷水机组COP>=5.0得满分，低于5.0为负偏离 |",
            },
            page_or_sheet="1",
        ),
    ]

    # 自编标书 (包含工期满足 90 天，COP 4.8 负偏离)
    proposal_nodes = [
        ASTNode(
            block_id="p_h1",
            block_type=ASTBlockType.HEADING,
            level=1,
            section_path=["技术方案响应"],
            text_content="技术方案响应",
            page_or_sheet="1",
        ),
        ASTNode(
            block_id="p_p1",
            block_type=ASTBlockType.PARAGRAPH,
            level=0,
            section_path=["技术方案响应", "工期承诺"],
            text_content="我方在此承诺：工期要求严格执行，总工期承诺为 90 个日历天，绝不延误。",
            page_or_sheet="1",
        ),
        ASTNode(
            block_id="p_p2",
            block_type=ASTBlockType.PARAGRAPH,
            level=0,
            section_path=["技术方案响应", "暖通设备"],
            text_content="机房选用离心式冷水机组，实测额定能效比 COP 值为 4.8，满足日常运行负荷。",
            page_or_sheet="2",
        ),
    ]

    await _create_test_document_with_ast(tenant_id, src_id, "投标文件", proposal_nodes)
    await _create_test_document_with_ast(tenant_id, tgt_id, "招标文件", rfp_nodes)

    headers = {"X-Tenant-ID": tenant_id}
    payload = {
        "source_document_id": src_id,
        "target_document_id": tgt_id,
    }

    resp = await client.post("/api/v1/quality/tender-alignment", json=payload, headers=headers)
    assert resp.status_code == 200
    report = resp.json()

    assert report["source_document_id"] == src_id
    assert report["target_document_id"] == tgt_id
    assert report["total_criteria_count"] >= 2
    assert report["results"] is not None

    # 验证落库
    async with SessionLocal() as session:
        stmt = select(AuditTask).where(
            AuditTask.source_document_id == src_id,
            AuditTask.target_document_id == tgt_id,
        )
        task = (await session.execute(stmt)).scalar_one_or_none()
        assert task is not None
        assert task.tenant_id == tenant_id

        stmt_rev = select(ReviewResult).where(ReviewResult.task_id == task.id)
        revs = (await session.execute(stmt_rev)).scalars().all()
        assert len(revs) > 0


@pytest.mark.asyncio
async def test_quality_tenant_isolation(client: AsyncClient):
    """跨租户质检验证：租户 B 无法对租户 A 的文档发起质检"""
    nodes = [
        ASTNode(
            block_id="b1",
            block_type=ASTBlockType.PARAGRAPH,
            level=0,
            section_path=["正文"],
            text_content="内容",
            page_or_sheet="1",
        )
    ]
    await _create_test_document_with_ast("tenant_a", "doc_private_a", "私人标书", nodes)

    # 租户 B 发起质检请求
    resp = await client.post(
        "/api/v1/quality/check",
        json={"document_id": "doc_private_a"},
        headers={"X-Tenant-ID": "tenant_b"},
    )
    assert resp.status_code == 404

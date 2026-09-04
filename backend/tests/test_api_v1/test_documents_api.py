"""
文档 API (POST /upload, GET /{doc_id}/status, GET /) 集成测试套件
验证:
1. DOCX/PDF 等多格式上传与 Celery eager 模式同步解析入库
2. 非法后缀与空文件防御拦截
3. 文档状态与切片/节点统计查询
4. 多租户隔离 (Tenant Alpha 上传，Tenant Beta 查询 404)
"""

import io
import zipfile
import pytest
from httpx import AsyncClient


def make_test_docx_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        ct_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
            '  <Default Extension="xml" ContentType="application/xml"/>\n'
            '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
            '  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>\n'
            '</Types>'
        )
        z.writestr("[Content_Types].xml", ct_xml)
        doc_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">\n'
            '  <w:body>\n'
            '    <w:p><w:pPr><w:pStyle w:val="1"/></w:pPr><w:r><w:t>第一章 总体技术方案</w:t></w:r></w:p>\n'
            '    <w:p><w:r><w:t>1.1 项目实施范围与标准规范。计划工期为90天，投资概算为5000万元。</w:t></w:r></w:p>\n'
            '  </w:body>\n'
            '</w:document>'
        )
        z.writestr("word/document.xml", doc_xml)
    return buf.getvalue()


def make_test_pdf_bytes() -> bytes:
    content = (
        "%PDF-1.7\n"
        "/Title (第一章 招标说明)\n"
        "1 0 obj\n"
        "<< /Type /Page >>\n"
        "BT /F1 12 Tf 100 700 Td (1.1 招标总体要求，工期要求<=90个日历天) Tj ET\n"
        "%%EOF\n"
    )
    return content.encode("utf-8")


@pytest.mark.asyncio
async def test_upload_docx_document(client: AsyncClient):
    """测试上传合法 DOCX 文档，验证返回 201 且完成解析"""
    docx_bytes = make_test_docx_bytes()
    files = {"file": ("proposal.docx", docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
    headers = {"X-Tenant-ID": "tenant_doc_a"}

    resp = await client.post("/api/v1/documents/upload", files=files, headers=headers)
    assert resp.status_code == 201
    data = resp.json()
    assert "document_id" in data
    assert data["file_name"] == "proposal.docx"
    assert data["file_type"] == "docx"
    assert data["parse_status"] == "success"

    # 查询状态
    doc_id = data["document_id"]
    status_resp = await client.get(f"/api/v1/documents/{doc_id}/status", headers=headers)
    assert status_resp.status_code == 200
    sdata = status_resp.json()
    assert sdata["document_id"] == doc_id
    assert sdata["parse_status"] == "success"
    assert sdata["total_chunks"] > 0
    assert sdata["ast_node_count"] > 0


@pytest.mark.asyncio
async def test_upload_pdf_document(client: AsyncClient):
    """测试上传合法 PDF 文档"""
    pdf_bytes = make_test_pdf_bytes()
    files = {"file": ("tender_rfp.pdf", pdf_bytes, "application/pdf")}
    headers = {"X-Tenant-ID": "tenant_doc_a"}

    resp = await client.post("/api/v1/documents/upload", files=files, headers=headers)
    assert resp.status_code == 201
    data = resp.json()
    assert data["file_name"] == "tender_rfp.pdf"
    assert data["file_type"] == "pdf"
    assert data["parse_status"] == "success"


@pytest.mark.asyncio
async def test_upload_unsupported_format(client: AsyncClient):
    """测试上传非法文件格式 (exe) 抛出 400"""
    files = {"file": ("malicious.exe", b"MZ\x90\x00\x03", "application/octet-stream")}
    headers = {"X-Tenant-ID": "tenant_doc_a"}

    resp = await client.post("/api/v1/documents/upload", files=files, headers=headers)
    assert resp.status_code == 400
    assert "不支持的文档格式" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_upload_empty_file(client: AsyncClient):
    """测试上传 0 字节空文件抛出 400"""
    files = {"file": ("empty.docx", b"", "application/octet-stream")}
    headers = {"X-Tenant-ID": "tenant_doc_a"}

    resp = await client.post("/api/v1/documents/upload", files=files, headers=headers)
    assert resp.status_code == 400
    assert "为空" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_document_tenant_isolation(client: AsyncClient):
    """多租户隔离验证：租户 A 上传的文档，租户 B 访问返回 404"""
    docx_bytes = make_test_docx_bytes()
    files = {"file": ("private_a.docx", docx_bytes, "application/octet-stream")}

    # 租户 A 上传
    resp = await client.post("/api/v1/documents/upload", files=files, headers={"X-Tenant-ID": "tenant_alpha"})
    assert resp.status_code == 201
    doc_id = resp.json()["document_id"]

    # 租户 A 查询成功
    resp_a = await client.get(f"/api/v1/documents/{doc_id}/status", headers={"X-Tenant-ID": "tenant_alpha"})
    assert resp_a.status_code == 200

    # 租户 B 查询返回 404
    resp_b = await client.get(f"/api/v1/documents/{doc_id}/status", headers={"X-Tenant-ID": "tenant_beta"})
    assert resp_b.status_code == 404


@pytest.mark.asyncio
async def test_document_list_query(client: AsyncClient):
    """测试当前租户文档列表分页查询"""
    docx_bytes = make_test_docx_bytes()
    headers = {"X-Tenant-ID": "tenant_list_test"}

    # 上传 2 个文档
    await client.post("/api/v1/documents/upload", files={"file": ("doc1.docx", docx_bytes, "application/octet-stream")}, headers=headers)
    await client.post("/api/v1/documents/upload", files={"file": ("doc2.docx", docx_bytes, "application/octet-stream")}, headers=headers)

    list_resp = await client.get("/api/v1/documents", headers=headers)
    assert list_resp.status_code == 200
    data = list_resp.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2

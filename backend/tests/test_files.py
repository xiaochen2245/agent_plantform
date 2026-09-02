"""契约 v4：上传链路（POST /api/chat/files）+ 发送时 Dify 转发。"""
import json

from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.dify.deps import get_dify
from app.main import app
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.upload_file import UploadFile
from tests.conftest import login
from tests.fake_dify import (
    SSE_OK,
    failing_upload_dify_client,
    fake_dify_client,
)


def _use(dify) -> None:
    app.dependency_overrides[get_dify] = lambda: dify


PDF = b"%PDF-1.4 fake pdf content for tests"


async def _upload(c: AsyncClient, content=PDF, name="报告.pdf", mime="application/pdf"):
    return await c.post(
        "/api/chat/files",
        files={"file": (name, content, mime)},
    )


async def _send_with_file(c: AsyncClient, file_id: str):
    async with c.stream(
        "POST",
        "/api/chat/send",
        json={"app_id": 1, "query": "帮我看看这份文件", "files": [file_id]},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines()]
    return lines


# ── 上传端点 ──────────────────────────────────────────────


async def test_upload_requires_auth(client: AsyncClient):
    resp = await client.post(
        "/api/chat/files", files={"file": ("a.pdf", PDF, "application/pdf")}
    )
    assert resp.status_code == 401


async def test_upload_success_returns_contract_shape(client: AsyncClient):
    await login(client)
    resp = await _upload(client)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["file_id"].startswith("f_")
    assert body["name"] == "报告.pdf"
    assert body["size"] == len(PDF)
    assert body["mime"] == "application/pdf"

    async with SessionLocal() as s:
        row = await s.get(UploadFile, body["file_id"])
        assert row is not None
        assert row.user_id == 1
        from pathlib import Path

        assert Path(row.storage_path).is_file()
        assert Path(row.storage_path).read_bytes() == PDF


async def test_upload_rejects_unsupported_mime(client: AsyncClient):
    await login(client)
    resp = await _upload(client, content=b"MZ...", name="a.exe", mime="application/x-msdownload")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "unsupported file type"


async def test_upload_rejects_missing_mime(client: AsyncClient):
    await login(client)
    resp = await client.post("/api/chat/files", files={"file": ("a.bin", b"xx", "")})
    assert resp.status_code == 400


async def test_upload_streaming_size_cap_413(client: AsyncClient, monkeypatch):
    from pathlib import Path

    before = set(p.name for p in Path(settings.UPLOAD_DIR).glob("*"))
    from app.files import router as files_router

    monkeypatch.setattr(files_router, "MAX_UPLOAD_BYTES", 64)
    await login(client)
    resp = await _upload(client, content=b"x" * 200)
    assert resp.status_code == 413
    # 半截文件不留盘（对比上传前后目录差异）
    after = set(p.name for p in Path(settings.UPLOAD_DIR).glob("*"))
    assert after == before


async def test_upload_sanitizes_traversal_filename(client: AsyncClient):
    await login(client)
    resp = await _upload(client, name="../../../../etc/passwd")
    assert resp.status_code == 201
    name = resp.json()["name"]
    assert "/" not in name and ".." not in name
    async with SessionLocal() as s:
        row = await s.get(UploadFile, resp.json()["file_id"])
        # 存储名为 uuid+安全后缀，不含任何用户可控路径分量
        import re
        from pathlib import Path

        stored = Path(row.storage_path)
        assert re.fullmatch(r"[0-9a-f]{32}\.[a-z0-9]{1,8}", stored.name)
        assert ".." not in stored.parts


# ── 发送时转发 ────────────────────────────────────────────


async def test_send_with_file_forwards_to_dify_and_persists(client: AsyncClient):
    captured: list = []
    _use(fake_dify_client(SSE_OK, captured=captured))
    await login(client)

    up = await _upload(client)
    file_id = up.json()["file_id"]
    lines = await _send_with_file(client, file_id)

    # SSE 正常流（不因附件断）
    joined = "\n".join(lines)
    assert "event: message" in joined and "event: agent_done" in joined

    # 1) 先打 /v1/files/upload（multipart、带 app key）
    upload_calls = [c for c in captured if str(c["url"]).endswith("/v1/files/upload")]
    assert len(upload_calls) == 1
    assert "Bearer demo-key" in upload_calls[0]["headers"]["authorization"]
    assert b"application/pdf" in upload_calls[0]["content"]

    # 2) 再打 chat-messages，files 参数为 Dify 形态
    chat_calls = [c for c in captured if "chat-messages" in c["url"]]
    assert len(chat_calls) == 1
    sent_files = chat_calls[0]["json"]["files"]
    assert sent_files == [
        {"type": "document", "transfer_method": "local_file", "upload_file_id": "dify-file-1"}
    ]

    # 3) 用户消息落 files 元数据；详情端点返回
    async with SessionLocal() as s:
        conv = (await s.execute(select(Conversation))).scalars().first()
        msg = (
            await s.execute(
                select(Message).where(Message.conversation_id == conv.id, Message.role == "user")
            )
        ).scalars().one()
        assert msg.files == [
            {
                "file_id": file_id,
                "name": "报告.pdf",
                "size": len(PDF),
                "mime": "application/pdf",
                "dify_file_id": "dify-file-1",
            }
        ]
        detail = await client.get(f"/api/conversations/{conv.id}/messages")
        assert detail.status_code == 200
        user_msg = [m for m in detail.json()["messages"] if m["role"] == "user"][0]
        assert user_msg["files"][0]["dify_file_id"] == "dify-file-1"


async def test_send_rejects_foreign_file(client: AsyncClient):
    _use(fake_dify_client(SSE_OK))
    await login(client)
    up = await _upload(client)
    file_id = up.json()["file_id"]

    # 换一个普通用户（非上传者）发送
    await client.post(
        "/api/admin/users",
        json={"name": "李雷", "email": "lei@company.com", "password": "secret123"},
    )
    await login(client, email="lei@company.com", password="secret123")
    resp = await client.post(
        "/api/chat/send",
        json={"app_id": 1, "query": "hi", "files": [file_id]},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "File not found"


async def test_send_unknown_file_404(client: AsyncClient):
    _use(fake_dify_client(SSE_OK))
    await login(client)
    resp = await client.post(
        "/api/chat/send", json={"app_id": 1, "query": "hi", "files": ["f_nope"]}
    )
    assert resp.status_code == 404


async def test_dify_upload_failure_skips_file_not_send(client: AsyncClient, caplog):
    _use(failing_upload_dify_client(SSE_OK))
    await login(client)
    up = await _upload(client)
    file_id = up.json()["file_id"]

    lines = await _send_with_file(client, file_id)
    joined = "\n".join(lines)
    assert "event: agent_done" in joined  # 发送未被阻断

    async with SessionLocal() as s:
        conv = (await s.execute(select(Conversation))).scalars().first()
        msg = (
            await s.execute(
                select(Message).where(Message.conversation_id == conv.id, Message.role == "user")
            )
        ).scalars().one()
        assert msg.files[0]["file_id"] == file_id
        assert msg.files[0]["dify_file_id"] is None


async def test_image_mime_maps_to_image_type(client: AsyncClient):
    captured: list = []
    _use(fake_dify_client(SSE_OK, captured=captured))
    await login(client)
    up = await _upload(client, content=b"\x89PNG...", name="截图.png", mime="image/png")
    await _send_with_file(client, up.json()["file_id"])
    chat_calls = [c for c in captured if "chat-messages" in c["url"]]
    assert chat_calls[0]["json"]["files"][0]["type"] == "image"


# ── B5：上传 TTL 清理 ──────────────────────────────────────────────────────
async def test_sweep_expired_uploads_removes_only_stale(tmp_path, monkeypatch):
    import os
    import time

    from app.files.cleanup import sweep_expired_uploads

    old = tmp_path / "old.bin"
    old.write_bytes(b"x")
    stale_ts = time.time() - 40 * 86400  # 40 天前
    os.utime(old, (stale_ts, stale_ts))
    fresh = tmp_path / "fresh.bin"
    fresh.write_bytes(b"y")

    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "UPLOAD_TTL_DAYS", 30)
    await sweep_expired_uploads()

    assert not old.exists()
    assert fresh.exists()


async def test_sweep_missing_dir_is_noop(tmp_path, monkeypatch):
    from app.files.cleanup import sweep_expired_uploads

    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "not-exist"))
    await sweep_expired_uploads()  # 不抛即通过

"""POST /api/chat/files（契约 v4）：上传对话附件。

安全约束（设计 §5.6 / §13.5）：
- 大小 ≤ 20MB：Content-Length 预检（multipart 开销留 64KB 余量）+ 流式累计双保险
- MIME 白名单六种；文件名清洗防路径穿越
- 存本地卷 uploads/<uuid>.<安全后缀>（入库元数据，发送时才转发 Dify）
"""
import re
import secrets
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.upload_file import UploadFile as UploadFileModel
from app.models.user import User
from app.schemas.chat import FileUploadedOut

router = APIRouter(prefix="/api/chat", tags=["files"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
_CONTENT_LENGTH_SLACK = 64 * 1024  # multipart 边界/字段开销

ALLOWED_MIMES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "text/markdown",
    "image/png",
    "image/jpeg",
}

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]")
_SAFE_EXT = re.compile(r"^[A-Za-z0-9]{1,8}$")


def sanitize_filename(name: str) -> str:
    """剥路径分量、替不安全字符、限长；空结果回退 'file'。"""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    base = _UNSAFE_CHARS.sub("_", base).lstrip(".")
    base = base[:120] or "file"
    return base


def _safe_ext(display_name: str) -> str:
    ext = display_name.rsplit(".", 1)[-1].lower() if "." in display_name else ""
    return ext if _SAFE_EXT.match(ext) else "bin"


@router.post("/files", status_code=status.HTTP_201_CREATED, response_model=FileUploadedOut)
async def upload_chat_file(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> FileUploadedOut:
    # 双层校验之一：Content-Length 预检（不含 body 的请求由之二兜底）
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES + _CONTENT_LENGTH_SLACK:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "file too large")

    mime = (file.content_type or "").split(";")[0].strip().lower()
    if mime not in ALLOWED_MIMES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unsupported file type")

    display_name = sanitize_filename(file.filename or "file")
    file_id = f"f_{secrets.token_hex(8)}"

    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    storage = upload_dir / f"{uuid.uuid4().hex}.{_safe_ext(display_name)}"

    total = 0
    try:
        with storage.open("wb") as out:
            while chunk := await file.read(1024 * 256):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:  # 双层校验之二：流式累计
                    raise HTTPException(
                        status.HTTP_413_CONTENT_TOO_LARGE, "file too large"
                    )
                out.write(chunk)
    except HTTPException:
        storage.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    db.add(
        UploadFileModel(
            id=file_id,
            user_id=user.id,
            name=display_name,
            size=total,
            mime=mime,
            storage_path=str(storage),
        )
    )
    await db.commit()

    return FileUploadedOut(file_id=file_id, name=display_name, size=total, mime=mime)

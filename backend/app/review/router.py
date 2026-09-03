"""功能① 审查路由（#30）：docx + 模板 → 结构化问题清单。"""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.auth.deps import current_user
from app.models.user import User
from app.review.ooxml import Docx, DocxFormatError
from app.review.rules import check

router = APIRouter(prefix="/api/review", tags=["review"])

MAX_REVIEW_BYTES = 50 * 1024 * 1024


@router.post("/docx")
async def review_docx(
    file: UploadFile = File(...),
    template: UploadFile = File(...),
    user: User = Depends(current_user),
) -> dict:
    """审查 file 对照 template（公司模板 OOXML 基准），返回问题清单+定位。"""
    data = await file.read()
    tpl = await template.read()
    for name, blob in (("file", data), ("template", tpl)):
        if len(blob) > MAX_REVIEW_BYTES:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"{name} exceeds 50MB")
        if not blob:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{name} is empty")
    try:
        return check(Docx(data), Docx(tpl))
    except DocxFormatError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e

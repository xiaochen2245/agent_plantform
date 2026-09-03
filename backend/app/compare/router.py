"""功能② 比对路由（#33：评分表结构化拆解；#34 比对流水线后续切片）。"""
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.deps import current_user
from app.compare.extract import ScoringTableExtractor
from app.compare.schema import ScoringTableQuery
from app.models.user import User
from app.ragflow.client import RagflowClient, RagflowError
from app.ragflow.deps import get_ragflow

router = APIRouter(prefix="/api/compare", tags=["compare"])


@router.post("/scoring-table")
async def extract_scoring_table(
    payload: ScoringTableQuery,
    user: User = Depends(current_user),
    client: RagflowClient = Depends(get_ragflow),
) -> dict:
    """{dataset_id, document_id} → 该文档评分表结构化 JSON（人审工作台输入）。
    未解析（无 chunks）→ 409；抽取失败（重试用尽）→ 502。"""
    try:
        chunks = await client.list_chunks(payload.dataset_id, payload.document_id)
    except RagflowError as e:
        if e.status_code in (401, 403):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "dataset not found") from e
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"ragflow upstream: {e.message[:200]}") from e
    if not chunks:
        raise HTTPException(status.HTTP_409_CONFLICT, "document not parsed yet (no chunks)")
    table = await ScoringTableExtractor().extract(chunks)
    if table is None:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "scoring table extraction failed")
    return table.model_dump()

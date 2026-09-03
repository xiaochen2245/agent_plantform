"""功能② 比对路由：#33 评分表结构化拆解 + #34 比对流水线与偏离建议。"""
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.deps import current_user
from app.compare.deviation import Comparator
from app.compare.extract import ScoringTableExtractor
from app.compare.schema import CompareQuery, ScoringTable, ScoringTableQuery
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


@router.post("/deviation")
async def compare_deviation(
    payload: CompareQuery,
    user: User = Depends(current_user),
    client: RagflowClient = Depends(get_ragflow),
) -> dict:
    """招标评分表 vs 投标响应 → 逐项 响应状态/偏离建议/依据（#34）。
    输出仅供人审，P1 不做自动结论。上游未解析 409 / 抽取或比对失败 502。"""
    try:
        sc_chunks = await client.list_chunks(payload.scoring.dataset_id, payload.scoring.document_id)
        rp_chunks = await client.list_chunks(payload.response.dataset_id, payload.response.document_id)
    except RagflowError as e:
        if e.status_code in (401, 403):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "dataset not found") from e
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"ragflow upstream: {e.message[:200]}") from e
    if not sc_chunks or not rp_chunks:
        raise HTTPException(status.HTTP_409_CONFLICT, "document not parsed yet (no chunks)")
    table = await ScoringTableExtractor().extract(sc_chunks)
    if table is None:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "scoring table extraction failed")
    result = await Comparator().compare(table, rp_chunks)
    if result is None:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "deviation comparison failed")
    return result.model_dump()

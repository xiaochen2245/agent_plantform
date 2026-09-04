"""
FastAPI v1 聚合总路由 (API v1 Master Router)
汇聚:
- /documents: 文档上传与解析状态
- /quality: 排版大纲质检与招投标对齐
- /workflow: 双智能体闭环反思、HITL 与 SSE
- /risk: 历史风险知识库与主动拦截
"""

from fastapi import APIRouter

from app.api.v1.documents import router as documents_router
from app.api.v1.quality import router as quality_router
from app.api.v1.risk import router as risk_router
from app.api.v1.workflow import router as workflow_router

api_v1_router = APIRouter()

api_v1_router.include_router(documents_router, prefix="/documents", tags=["documents"])
api_v1_router.include_router(quality_router, prefix="/quality", tags=["quality"])
api_v1_router.include_router(workflow_router, prefix="/workflow", tags=["workflow"])
api_v1_router.include_router(risk_router, prefix="/risk", tags=["risk"])

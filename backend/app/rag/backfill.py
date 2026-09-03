"""
上下文回填引擎 (ContextBackfiller)
负责在父子层级检索架构中实现精准的上下文向上回填：
1. 从命中的高精度 Child/Table 切片向上解析其所属的唯一 Parent Chunk (1024~2048 tokens)；
2. 保持排名优先级：命中最高分子切片的父切片优先排列；
3. 保留细粒度引述证据链 (CitationAnchor)：记录子切片的高亮原句、命中得分、页码及包围盒坐标；
4. 租户硬隔离控制：严格校验 tenant_id，杜绝越权获取父切片；
5. Token 预算管理与结构化 Prompt 格式化。
"""

from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_rag import ChunkLevel, DocumentChunk
from app.rag.hybrid_search import SearchResultItem
from app.rag.reranker import RerankResult


class CitationAnchor(BaseModel):
    """原子级引述溯源锚点 (用于下游 Critic Agent 证据链校验与前端高亮定位)"""
    child_chunk_id: str = Field(..., description="命中子切片 ID")
    content_quote: str = Field(..., description="命中原子条款/命题原文")
    relevance_score: float = Field(..., description="命中得分")
    page_number: Optional[int] = Field(None, description="物理页码")
    section_path: str = Field(..., description="小节路径")
    bbox_coordinates: Dict[str, Any] = Field(default_factory=dict, description="定位包围盒坐标")


class ParentContextItem(BaseModel):
    """回填后的完备父切片单元"""
    parent_chunk_id: str = Field(..., description="父切片 ID")
    document_id: str = Field(..., description="文档 ID")
    section_path: str = Field(..., description="章节大纲路径")
    content: str = Field(..., description="1024~2048 tokens 完备大纲与正文 Markdown")
    token_count: int = Field(..., description="Parent Token 数量")
    page_range: List[int] = Field(default_factory=list, description="覆盖页码区间 [min_page, max_page]")
    max_child_score: float = Field(..., description="该父切片下命中子切片的最优得分")
    child_citations: List[CitationAnchor] = Field(default_factory=list, description="归属该父切片的所有命中子切片证据链")


class BackfilledContext(BaseModel):
    """回填组装完成的上下文总包"""
    parents: List[ParentContextItem] = Field(default_factory=list, description="去重并按优先级排序的父切片列表")
    total_parent_tokens: int = Field(0, description="回填父切片累计总 Token 消耗")
    unique_parent_count: int = Field(0, description="去重父切片总数")
    formatted_prompt_context: str = Field("", description="直接注入大模型/LangGraph 状态机的结构化 Markdown 上下文")


class ContextBackfiller:
    """
    企业级上下文回填器 (Context Backfiller)
    实现从细粒度原子子切片到完整语义父切片的平滑跃升
    """

    def __init__(self, default_max_parent_tokens: int = 8192):
        self.default_max_parent_tokens = default_max_parent_tokens

    async def backfill(
        self,
        session: AsyncSession,
        hits: List[Union[RerankResult, SearchResultItem]],
        tenant_id: str,
        max_parent_tokens: Optional[int] = None,
    ) -> BackfilledContext:
        """
        核心回填算法:
        1. 遍历命中列表，收集所有 parent_chunk_id，建立 parent_id -> List[ChildHit] 映射；
        2. 按子切片最高打分对父切片进行优先级排序；
        3. 异步批量拉取数据库中的 Parent Chunks (绑定 tenant_id 硬隔离)；
        4. 装配 CitationAnchor 证据链与 Token 预算截断；
        5. 生成用于 LangGraph 状态机的标准 Markdown 提示文本。
        """
        if not hits:
            return BackfilledContext()

        token_budget = max_parent_tokens or self.default_max_parent_tokens

        # 阶段 1: 提炼父切片关联与证据链
        parent_id_order: List[str] = []
        parent_child_map: Dict[str, List[CitationAnchor]] = {}
        parent_best_score: Dict[str, float] = {}

        for hit in hits:
            # 兼容 RerankResult 与 SearchResultItem
            cid = hit.chunk_id
            score = getattr(hit, "relevance_score", getattr(hit, "rrf_score", 0.0))
            pid = hit.parent_chunk_id or cid  # 孤立切片回退为其自身

            citation = CitationAnchor(
                child_chunk_id=cid,
                content_quote=hit.content,
                relevance_score=round(score, 4),
                page_number=hit.page_number,
                section_path=hit.section_path,
                bbox_coordinates=hit.bbox_coordinates,
            )

            if pid not in parent_child_map:
                parent_child_map[pid] = []
                parent_id_order.append(pid)
                parent_best_score[pid] = score
            else:
                if score > parent_best_score[pid]:
                    parent_best_score[pid] = score

            parent_child_map[pid].append(citation)

        # 阶段 2: 依据最高子切片得分对父切片二次排序
        sorted_parent_ids = sorted(
            parent_id_order,
            key=lambda p: parent_best_score[p],
            reverse=True,
        )

        # 阶段 3: 数据库批量查询父切片 (强制 tenant_id 硬隔离)
        stmt = (
            select(DocumentChunk)
            .where(
                DocumentChunk.id.in_(sorted_parent_ids),
                DocumentChunk.tenant_id == tenant_id,
            )
        )
        result = await session.execute(stmt)
        found_chunks = result.scalars().all()
        chunk_lookup = {c.id: c for c in found_chunks}

        # 阶段 4: 组装 ParentContextItem 并校验 Token 预算
        parent_items: List[ParentContextItem] = []
        cumulative_tokens = 0
        prompt_sections: List[str] = []

        for p_idx, pid in enumerate(sorted_parent_ids, start=1):
            chunk_obj = chunk_lookup.get(pid)
            if not chunk_obj:
                # 若父切片未在库中查得 (可能切片时未保存父级)，回退使用命中的首个子切片构造降级上下文
                first_cit = parent_child_map[pid][0]
                content = first_cit.content_quote
                sec_path = first_cit.section_path
                page_no = first_cit.page_number or 1
                page_range = [page_no, page_no]
                tok_cnt = len(content) // 2  # 快速估算
                doc_id = getattr(hits[0], "document_id", "unknown")
            else:
                content = chunk_obj.content
                sec_path = chunk_obj.section_path
                page_no = chunk_obj.page_number or 1
                meta = chunk_obj.chunk_metadata or {}
                page_range = meta.get("page_range", [page_no, page_no])
                tok_cnt = chunk_obj.token_count or (len(content) // 2)
                doc_id = chunk_obj.document_id

            # 预算检查
            if cumulative_tokens + tok_cnt > token_budget and parent_items:
                # 超出最大 Token 预算，停止继续装载更多 Parent
                break

            citations = parent_child_map[pid]
            p_item = ParentContextItem(
                parent_chunk_id=pid,
                document_id=doc_id,
                section_path=sec_path,
                content=content,
                token_count=tok_cnt,
                page_range=page_range,
                max_child_score=parent_best_score[pid],
                child_citations=citations,
            )
            parent_items.append(p_item)
            cumulative_tokens += tok_cnt

            # 构造结构化 Markdown 块
            pages_str = f"P{page_range[0]}" if page_range[0] == page_range[1] else f"P{page_range[0]}~P{page_range[1]}"
            prompt_section = (
                f"### [知识库上下文 {p_idx}] 章节: {sec_path} (页码: {pages_str})\n"
                f"{content}\n"
            )
            prompt_sections.append(prompt_section)

        formatted_context = "\n".join(prompt_sections)

        return BackfilledContext(
            parents=parent_items,
            total_parent_tokens=cumulative_tokens,
            unique_parent_count=len(parent_items),
            formatted_prompt_context=formatted_context,
        )

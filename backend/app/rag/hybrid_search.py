"""
混合检索系统 (HybridSearchEngine)
实现工业级三路协同检索与倒数排名融合 (RRF):
1. pgvector HNSW 稠密检索 (余弦距离 <=>，1536 维) + SQLite 内存向量计算无缝降级；
2. BM25 稀疏检索 (jieba 分词 / 工程型号特化分词 + BM25Okapi 关键词打分)；
3. Reciprocal Rank Fusion (RRF) 倒数排名融合算法 (k=60, dense: 0.6, sparse: 0.4)；
4. 租户硬隔离：所有查询均锁定 tenant_id 与 Child/Table 细粒度切片。
"""

from collections import Counter
import math
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_rag import (
    ChunkLevel,
    DocumentChunk,
    HAS_PGVECTOR,
)
from app.rag.embedding import EmbeddingService, get_embedding_service


class SearchResultItem(BaseModel):
    """单条检索命中候选实体"""
    chunk_id: str = Field(..., description="切片唯一 ID")
    document_id: str = Field(..., description="所属文档 ID")
    parent_chunk_id: Optional[str] = Field(None, description="回填关联父切片 ID")
    content: str = Field(..., description="切片文本内容")
    section_path: str = Field(..., description="大纲章节路径")
    page_number: Optional[int] = Field(None, description="物理页码")
    bbox_coordinates: Dict[str, Any] = Field(default_factory=dict, description="定位坐标")
    chunk_metadata: Dict[str, Any] = Field(default_factory=dict, description="业务元数据")
    
    # 打分细节
    dense_score: Optional[float] = Field(None, description="稠密余弦相似度 [0.0 ~ 1.0]")
    sparse_score: Optional[float] = Field(None, description="BM25 原始分值")
    rrf_score: float = Field(0.0, description="RRF 融合综合得分")

    model_config = {"arbitrary_types_allowed": True}


class BM25Tokenizer:
    """
    针对工程公文、招投标书、工程量清单与设计图纸的特化分词器。
    1. 优先使用 jieba 进行中文分词；
    2. 若未安装 jieba，则采用无缝降级正则分词；
    3. 特化保留工程参数、设备型号、定额编码与数值单位 (如 '90天', '1200万元', 'C30', '45kW', 'GB/T 38330')。
    """

    _HAS_JIEBA = False
    try:
        import jieba
        _HAS_JIEBA = True
    except ImportError:
        _HAS_JIEBA = False

    # 抽取特殊规格、设备型号、指标编码的正则
    _SPEC_PATTERN = re.compile(
        r'[A-Za-z0-9_\-\./]+(?:kW|MW|m²|m³|MPa|mm|cm|m|kg|t|d|h|天|万元|元|%|dB)?'
    )
    _CHINESE_PATTERN = re.compile(r'[\u4e00-\u9fff]+')

    @classmethod
    def tokenize(cls, text: str) -> List[str]:
        if not text:
            return []

        text_lower = text.lower()

        if cls._HAS_JIEBA:
            import jieba
            tokens = [t.strip() for t in jieba.cut_for_search(text_lower) if t.strip()]
        else:
            tokens: List[str] = []
            # 提取英文、数字与规格
            specs = cls._SPEC_PATTERN.findall(text_lower)
            tokens.extend([s for s in specs if s])
            # 提取中文字符串
            cn_parts = cls._CHINESE_PATTERN.findall(text_lower)
            for part in cn_parts:
                # 产生单字与 2-gram
                tokens.extend(list(part))
                if len(part) > 1:
                    tokens.extend([part[i:i+2] for i in range(len(part) - 1)])

        # 过滤过短无意义停用字符
        return [t for t in tokens if len(t) > 0 and t not in {" ", "\t", "\n"}]


class InMemoryBM25:
    """
    轻量级高保真 BM25Okapi 检索实现 (零外部 C/Java 依赖)
    公式:
    IDF(q_i) = ln((N - n(q_i) + 0.5) / (n(q_i) + 0.5) + 1.0)
    Score(D, Q) = sum( IDF(q_i) * (f(q_i, D) * (k1 + 1)) / (f(q_i, D) + k1 * (1 - b + b * (|D| / avgdl))) )
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_ids: List[str] = []
        self.doc_lengths: List[int] = []
        self.avg_doc_len: float = 0.0
        self.doc_count: int = 0
        self.inverted_index: Dict[str, List[Tuple[int, int]]] = {}  # term -> [(doc_index, tf)]
        self.idf_cache: Dict[str, float] = {}

    def fit(self, corpus: List[Tuple[str, str]]) -> None:
        """输入 (doc_id, text) 构建索引"""
        self.doc_ids = []
        self.doc_lengths = []
        self.inverted_index = {}
        self.idf_cache = {}

        total_length = 0
        doc_term_freqs: List[Counter[str]] = []

        for doc_id, text in corpus:
            tokens = BM25Tokenizer.tokenize(text)
            length = len(tokens)
            self.doc_ids.append(doc_id)
            self.doc_lengths.append(length)
            total_length += length
            doc_term_freqs.append(Counter(tokens))

        self.doc_count = len(self.doc_ids)
        if self.doc_count == 0:
            return

        self.avg_doc_len = total_length / self.doc_count

        # 建立倒排索引
        for doc_idx, tf_counter in enumerate(doc_term_freqs):
            for term, count in tf_counter.items():
                if term not in self.inverted_index:
                    self.inverted_index[term] = []
                self.inverted_index[term].append((doc_idx, count))

        # 计算所有词项的 IDF
        for term, posting in self.inverted_index.items():
            df = len(posting)
            idf = math.log((self.doc_count - df + 0.5) / (df + 0.5) + 1.0)
            self.idf_cache[term] = max(idf, 1e-4)

    def search(self, query: str, top_k: int = 20) -> List[Tuple[str, float]]:
        """执行 BM25 打分并返回 Top-K (doc_id, bm25_score)"""
        if self.doc_count == 0:
            return []

        query_tokens = BM25Tokenizer.tokenize(query)
        if not query_tokens:
            return []

        scores = [0.0] * self.doc_count

        for q_term in query_tokens:
            if q_term not in self.inverted_index:
                continue

            idf = self.idf_cache[q_term]
            posting = self.inverted_index[q_term]

            for doc_idx, tf in posting:
                doc_len = self.doc_lengths[doc_idx]
                denom = tf + self.k1 * (1.0 - self.b + self.b * (doc_len / self.avg_doc_len))
                score_term = idf * (tf * (self.k1 + 1.0)) / denom
                scores[doc_idx] += score_term

        # 取分值大于 0 的前 top_k
        ranked = [(self.doc_ids[i], scores[i]) for i in range(self.doc_count) if scores[i] > 0]
        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked[:top_k]


class HybridSearchEngine:
    """
    生产级混合检索引擎 (Hybrid Search Engine)
    结合 pgvector HNSW 向量检索与 BM25 词法检索，使用 RRF (Reciprocal Rank Fusion) 融合
    """

    def __init__(
        self,
        embedding_service: Optional[EmbeddingService] = None,
        rrf_k: int = 60,
        dense_weight: float = 0.6,
        sparse_weight: float = 0.4,
        dense_top_k: int = 20,
        sparse_top_k: int = 20,
    ):
        self.embedding_service = embedding_service or get_embedding_service()
        self.rrf_k = rrf_k
        self.dense_weight = dense_weight
        self.sparse_weight = sparse_weight
        self.dense_top_k = dense_top_k
        self.sparse_top_k = sparse_top_k

    async def search(
        self,
        session: AsyncSession,
        tenant_id: str,
        query: str,
        top_k: int = 20,
        document_id: Optional[str] = None,
    ) -> List[SearchResultItem]:
        """
        全流程检索执行:
        1. 获取查询的 1536 维向量；
        2. 执行稠密检索 (pgvector HNSW / 内存余弦降级)；
        3. 执行稀疏检索 (BM25Okapi)；
        4. 执行 RRF 融合打分，返回 Top-K 命中。
        """
        if not query.strip():
            return []

        # 1. 异步获取向量
        query_vec = await self.embedding_service.embed_query(query)

        # 2. 稠密与稀疏并行/流式检索
        dense_results = await self._search_dense(
            session=session,
            tenant_id=tenant_id,
            query_vector=query_vec,
            top_k=self.dense_top_k,
            document_id=document_id,
        )

        sparse_results = await self._search_sparse(
            session=session,
            tenant_id=tenant_id,
            query=query,
            top_k=self.sparse_top_k,
            document_id=document_id,
        )

        # 3. 倒数排名融合 (RRF)
        fused_items = self._reciprocal_rank_fusion(
            dense_items=dense_results,
            sparse_items=sparse_results,
            top_k=top_k,
        )

        return fused_items

    # -----------------------------------------------------------------------
    # 稠密检索实现 (带 PostgreSQL pgvector 与 SQLite 自动分支)
    # -----------------------------------------------------------------------

    async def _search_dense(
        self,
        session: AsyncSession,
        tenant_id: str,
        query_vector: List[float],
        top_k: int,
        document_id: Optional[str] = None,
    ) -> List[SearchResultItem]:
        """
        稠密检索：
        - PostgreSQL + pgvector: 使用原生 <=> 余弦距离操作符与 HNSW 索引
        - SQLite / 测试环境: 查询当前租户所有 Child 切片，在内存中计算余弦相似度并排序
        """
        bind = session.bind
        dialect_name = bind.dialect.name if bind else "sqlite"

        if dialect_name == "postgresql" and HAS_PGVECTOR:
            return await self._search_dense_postgresql(
                session=session,
                tenant_id=tenant_id,
                query_vector=query_vector,
                top_k=top_k,
                document_id=document_id,
            )
        else:
            return await self._search_dense_in_memory_fallback(
                session=session,
                tenant_id=tenant_id,
                query_vector=query_vector,
                top_k=top_k,
                document_id=document_id,
            )

    async def _search_dense_postgresql(
        self,
        session: AsyncSession,
        tenant_id: str,
        query_vector: List[float],
        top_k: int,
        document_id: Optional[str] = None,
    ) -> List[SearchResultItem]:
        """PostgreSQL pgvector 原生余弦距离加速检索"""
        # 利用 pgvector 的 cosine_distance 方法生成 <=> 操作符
        distance_col = DocumentChunk.embedding.cosine_distance(query_vector).label("distance")

        stmt = (
            select(DocumentChunk, distance_col)
            .where(
                DocumentChunk.tenant_id == tenant_id,
                DocumentChunk.chunk_level.in_([ChunkLevel.CHILD, ChunkLevel.TABLE]),
                DocumentChunk.embedding.isnot(None),
            )
        )
        if document_id:
            stmt = stmt.where(DocumentChunk.document_id == document_id)

        stmt = stmt.order_by(distance_col).limit(top_k)
        result = await session.execute(stmt)
        rows = result.all()

        items: List[SearchResultItem] = []
        for chunk_obj, dist in rows:
            # cosine_distance: 0 表示完全一致，2 表示完全相反；相似度 = 1 - (dist / 2) 或 max(0, 1 - dist)
            cos_sim = max(0.0, 1.0 - float(dist))
            items.append(
                SearchResultItem(
                    chunk_id=chunk_obj.id,
                    document_id=chunk_obj.document_id,
                    parent_chunk_id=chunk_obj.parent_chunk_id,
                    content=chunk_obj.content,
                    section_path=chunk_obj.section_path,
                    page_number=chunk_obj.page_number,
                    bbox_coordinates=chunk_obj.bbox_coordinates or {},
                    chunk_metadata=chunk_obj.chunk_metadata or {},
                    dense_score=cos_sim,
                )
            )
        return items

    async def _search_dense_in_memory_fallback(
        self,
        session: AsyncSession,
        tenant_id: str,
        query_vector: List[float],
        top_k: int,
        document_id: Optional[str] = None,
    ) -> List[SearchResultItem]:
        """SQLite / 本地测试平滑降级：在应用层计算余弦相似度"""
        stmt = (
            select(DocumentChunk)
            .where(
                DocumentChunk.tenant_id == tenant_id,
                DocumentChunk.chunk_level.in_([ChunkLevel.CHILD, ChunkLevel.TABLE]),
                DocumentChunk.embedding.isnot(None),
            )
        )
        if document_id:
            stmt = stmt.where(DocumentChunk.document_id == document_id)

        result = await session.execute(stmt)
        chunks = result.scalars().all()

        scored_candidates: List[Tuple[DocumentChunk, float]] = []

        for chk in chunks:
            vec = EmbeddingService.parse_embedding_vector(chk.embedding)
            if not vec:
                continue
            # 计算余弦相似度 (假设两者均为 L2-normalized，点积即为 cosine)
            dot_product = sum(a * b for a, b in zip(query_vector, vec))
            scored_candidates.append((chk, float(dot_product)))

        scored_candidates.sort(key=lambda x: x[1], reverse=True)
        top_matches = scored_candidates[:top_k]

        items: List[SearchResultItem] = []
        for chk, score in top_matches:
            items.append(
                SearchResultItem(
                    chunk_id=chk.id,
                    document_id=chk.document_id,
                    parent_chunk_id=chk.parent_chunk_id,
                    content=chk.content,
                    section_path=chk.section_path,
                    page_number=chk.page_number,
                    bbox_coordinates=chk.bbox_coordinates or {},
                    chunk_metadata=chk.chunk_metadata or {},
                    dense_score=score,
                )
            )
        return items

    # -----------------------------------------------------------------------
    # 稀疏检索实现 (BM25)
    # -----------------------------------------------------------------------

    async def _search_sparse(
        self,
        session: AsyncSession,
        tenant_id: str,
        query: str,
        top_k: int,
        document_id: Optional[str] = None,
    ) -> List[SearchResultItem]:
        """
        稀疏检索：
        加载当前租户的目标候选切片，构建或应用内存 BM25 索引
        """
        stmt = (
            select(DocumentChunk)
            .where(
                DocumentChunk.tenant_id == tenant_id,
                DocumentChunk.chunk_level.in_([ChunkLevel.CHILD, ChunkLevel.TABLE]),
            )
        )
        if document_id:
            stmt = stmt.where(DocumentChunk.document_id == document_id)

        result = await session.execute(stmt)
        chunks = result.scalars().all()

        if not chunks:
            return []

        chunk_dict = {c.id: c for c in chunks}
        corpus = [(c.id, c.content) for c in chunks]

        bm25 = InMemoryBM25()
        bm25.fit(corpus)
        ranked_hits = bm25.search(query, top_k=top_k)

        items: List[SearchResultItem] = []
        for cid, score in ranked_hits:
            c = chunk_dict[cid]
            items.append(
                SearchResultItem(
                    chunk_id=c.id,
                    document_id=c.document_id,
                    parent_chunk_id=c.parent_chunk_id,
                    content=c.content,
                    section_path=c.section_path,
                    page_number=c.page_number,
                    bbox_coordinates=c.bbox_coordinates or {},
                    chunk_metadata=c.chunk_metadata or {},
                    sparse_score=score,
                )
            )
        return items

    # -----------------------------------------------------------------------
    # 倒数排名融合 (Reciprocal Rank Fusion)
    # -----------------------------------------------------------------------

    def _reciprocal_rank_fusion(
        self,
        dense_items: List[SearchResultItem],
        sparse_items: List[SearchResultItem],
        top_k: int,
    ) -> List[SearchResultItem]:
        """
        RRF 公式:
        RRF_score(d) = (w_dense / (k + rank_dense)) + (w_sparse / (k + rank_sparse))
        """
        rrf_map: Dict[str, float] = {}
        item_registry: Dict[str, SearchResultItem] = {}

        # 1. 记录 Dense 排名
        for rank, item in enumerate(dense_items, start=1):
            cid = item.chunk_id
            item_registry[cid] = item
            rrf_contrib = self.dense_weight / (self.rrf_k + rank)
            rrf_map[cid] = rrf_map.get(cid, 0.0) + rrf_contrib

        # 2. 记录 Sparse 排名
        for rank, item in enumerate(sparse_items, start=1):
            cid = item.chunk_id
            if cid not in item_registry:
                item_registry[cid] = item
            else:
                # 合并打分
                item_registry[cid].sparse_score = item.sparse_score
            rrf_contrib = self.sparse_weight / (self.rrf_k + rank)
            rrf_map[cid] = rrf_map.get(cid, 0.0) + rrf_contrib

        # 3. 排序生成融合结果
        sorted_ids = sorted(rrf_map.keys(), key=lambda cid: rrf_map[cid], reverse=True)
        final_items: List[SearchResultItem] = []

        for cid in sorted_ids[:top_k]:
            item = item_registry[cid]
            item.rrf_score = rrf_map[cid]
            final_items.append(item)

        return final_items

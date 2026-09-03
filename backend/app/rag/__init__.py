"""
企业级 Parent-Child RAG 知识库检索包
提供文档切片、稠密嵌入、混合检索、深度精排、上下文回填与 PostgreSQL 16+ RLS 租户硬隔离全链路能力。
"""

from app.rag.chunker import (
    ChunkingConfig,
    ParentChildChunker,
    TokenCounter,
)
from app.rag.embedding import (
    BaseEmbeddingProvider,
    EmbeddingService,
    MockDeterministicEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    get_embedding_service,
)
from app.rag.hybrid_search import (
    BM25Tokenizer,
    HybridSearchEngine,
    InMemoryBM25,
    SearchResultItem,
)
from app.rag.reranker import (
    BaseReranker,
    CrossEncoderReranker,
    HeuristicCrossEncoderReranker,
    ModelCrossEncoderReranker,
    RerankResult,
)
from app.rag.backfill import (
    BackfilledContext,
    CitationAnchor,
    ContextBackfiller,
    ParentContextItem,
)
from app.rag.tenant_rls import (
    RLS_PROTECTED_TABLES,
    TenantRLSManager,
    generate_rls_sql,
)

__all__ = [
    # Chunker
    "ChunkingConfig",
    "ParentChildChunker",
    "TokenCounter",
    # Embedding
    "BaseEmbeddingProvider",
    "EmbeddingService",
    "MockDeterministicEmbeddingProvider",
    "OpenAICompatibleEmbeddingProvider",
    "get_embedding_service",
    # Hybrid Search
    "BM25Tokenizer",
    "HybridSearchEngine",
    "InMemoryBM25",
    "SearchResultItem",
    # Reranker
    "BaseReranker",
    "CrossEncoderReranker",
    "HeuristicCrossEncoderReranker",
    "ModelCrossEncoderReranker",
    "RerankResult",
    # Backfill
    "BackfilledContext",
    "CitationAnchor",
    "ContextBackfiller",
    "ParentContextItem",
    # Tenant RLS
    "RLS_PROTECTED_TABLES",
    "TenantRLSManager",
    "generate_rls_sql",
]

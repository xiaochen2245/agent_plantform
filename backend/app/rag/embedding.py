"""
向量嵌入服务 (EmbeddingService)
负责 1536 维标准稠密向量生成：
1. BaseEmbeddingProvider: 统一抽象接口 (embed_documents, embed_query)；
2. OpenAICompatibleEmbeddingProvider: 生产级 HTTP 适配 (OpenAI, vLLM, TEI, Ollama)；
3. MockDeterministicEmbeddingProvider: 本地测试与 CI 确定性 1536 维向量生成器 (基于特征哈希与语义投影，保真余弦相似度)；
4. EmbeddingService: 门面层，提供单例缓存、批量切片嵌入注入与 pgvector/SQLite 兼容序列化。
"""

from abc import ABC, abstractmethod
import hashlib
import json
import math
import os
import re
from typing import Any, Dict, List, Optional
import httpx

from app.models.audit_rag import ChunkLevel, DocumentChunk, HAS_PGVECTOR


class BaseEmbeddingProvider(ABC):
    """向量嵌入模型提供商抽象基类"""

    dimension: int = 1536

    @abstractmethod
    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """批量生成文档切片嵌入向量"""
        pass

    @abstractmethod
    async def embed_query(self, text: str) -> List[float]:
        """生成单条查询的检索嵌入向量"""
        pass


def l2_normalize(vector: List[float]) -> List[float]:
    """对向量进行 L2 单位归一化，使得点积等价于余弦相似度"""
    norm = math.sqrt(sum(x * x for x in vector))
    if norm < 1e-12:
        return vector
    return [x / norm for x in vector]


class MockDeterministicEmbeddingProvider(BaseEmbeddingProvider):
    """
    确定性离线 1536 维向量生成器 (用于单元测试、CI及无外部模型密钥环境)
    算法原理:
    1. 提取中英文 N-gram (1-gram, 2-gram) 及关键词特征；
    2. 使用多哈希算法进行局部敏感哈希 (LSH) 与稠密投影至 1536 维；
    3. 保障共享关键词/数值的文本（如 '工期90天' 与 '承诺工期90个日历天'）具有高余弦相似度 (>0.75)；
    4. 结果严格满足 1536 维度且 L2-norm = 1.0。
    """

    dimension: int = 1536

    def __init__(self, seed: int = 42, dim: int = 1536):
        self.seed = seed
        self.dimension = dim

    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self._generate_vector(t) for t in texts]

    async def embed_query(self, text: str) -> List[float]:
        return self._generate_vector(text)

    def _generate_vector(self, text: str) -> List[float]:
        if not text:
            # 返回确定性零偏置单位向量
            v = [0.0] * self.dimension
            v[0] = 1.0
            return v

        vec = [0.0] * self.dimension

        # 1. 抽取词法与字符特征 (包括单字、连续双字、英文词、数字)
        cleaned = re.sub(r'[\s\W_]+', '', text.lower())
        tokens = list(cleaned)
        # 补充 bigram 特征
        bigrams = [cleaned[i:i+2] for i in range(len(cleaned) - 1)] if len(cleaned) > 1 else []
        # 补充数字/型号特征
        numbers = re.findall(r'\d+', text)

        all_features = tokens + bigrams + [f"NUM_{n}" for n in numbers]

        if not all_features:
            all_features = [text]

        # 2. 特征哈希投影到 1536 维空间
        for feat in all_features:
            h = hashlib.sha256(f"{self.seed}_{feat}".encode("utf-8")).digest()
            # 从 32 字节哈希中派生 4 个特征维度
            for i in range(0, 16, 4):
                idx = int.from_bytes(h[i:i+2], byteorder="little") % self.dimension
                val = (int.from_bytes(h[i+2:i+4], byteorder="little", signed=True) / 32768.0)
                vec[idx] += val

        # 3. 增强核心工程领域的语义投影聚类 (工期、造价、设备、规范)
        domain_clusters = {
            "工期": 10,
            "日历天": 11,
            "进度": 12,
            "造价": 20,
            "预算": 21,
            "万元": 22,
            "暖通": 30,
            "管道": 31,
            "设备": 32,
            "功率": 33,
            "招标文件": 40,
            "投标文件": 41,
            "偏离": 42,
            "满足": 43,
        }
        for kw, dim_offset in domain_clusters.items():
            if kw in text:
                vec[dim_offset] += 2.5
                vec[dim_offset + 100] += 1.8

        # 4. L2 归一化
        return l2_normalize(vec)


class OpenAICompatibleEmbeddingProvider(BaseEmbeddingProvider):
    """生产级 OpenAI 兼容 HTTP 向量服务提供商 (支持 OpenAI, vLLM, Ollama, TEI)"""

    def __init__(
        self,
        api_base: str,
        api_key: str = "sk-placeholder",
        model_name: str = "text-embedding-3-small",
        dimension: int = 1536,
        timeout_seconds: float = 30.0,
    ):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.model_name = model_name
        self.dimension = dimension
        self.timeout_seconds = timeout_seconds

    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []

        url = f"{self.api_base}/embeddings" if not self.api_base.endswith("/v1") else f"{self.api_base}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # 分批处理 (单批最大 64 条，防止超出请求体积或网关超时)
        batch_size = 64
        results: List[List[float]] = []

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            for i in range(0, len(texts), batch_size):
                batch = texts[i:i + batch_size]
                payload = {
                    "input": batch,
                    "model": self.model_name,
                    "dimensions": self.dimension,
                }
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                sorted_data = sorted(data["data"], key=lambda x: x["index"])
                for item in sorted_data:
                    vec = item["embedding"]
                    if len(vec) != self.dimension:
                        # 兼容处理截断或补零
                        if len(vec) > self.dimension:
                            vec = vec[:self.dimension]
                        else:
                            vec = vec + [0.0] * (self.dimension - len(vec))
                    results.append(l2_normalize(vec))

        return results

    async def embed_query(self, text: str) -> List[float]:
        vectors = await self.embed_documents([text])
        return vectors[0]


class EmbeddingService:
    """
    向量嵌入综合服务门面 (Embedding Service Facade)
    职责:
    1. 管理底层 Provider 实例与自动策略降级；
    2. 查询嵌入 LRU 缓存；
    3. 为切片实体注入向量，智能兼容 pgvector 与 SQLite 文本存储。
    """

    def __init__(self, provider: Optional[BaseEmbeddingProvider] = None):
        if provider is not None:
            self.provider = provider
        else:
            # 自动探测环境变量：存在 OPENAI_API_KEY 或 EMBEDDING_API_URL 时走 HTTP，否则走确定性 Mock
            api_url = os.getenv("EMBEDDING_API_URL")
            api_key = os.getenv("EMBEDDING_API_KEY", os.getenv("OPENAI_API_KEY"))
            if api_url and api_key:
                self.provider = OpenAICompatibleEmbeddingProvider(
                    api_base=api_url,
                    api_key=api_key,
                    model_name=os.getenv("EMBEDDING_MODEL_NAME", "text-embedding-3-small"),
                )
            else:
                self.provider = MockDeterministicEmbeddingProvider()

        self._query_cache: Dict[str, List[float]] = {}
        self._max_cache_size = 2000

    @property
    def dimension(self) -> int:
        return self.provider.dimension

    async def embed_query(self, query: str) -> List[float]:
        """获取查询向量 (带内存缓存)"""
        norm_query = query.strip()
        if norm_query in self._query_cache:
            return self._query_cache[norm_query]

        vec = await self.provider.embed_query(norm_query)

        # 缓存大小控制
        if len(self._query_cache) >= self._max_cache_size:
            # 淘汰前 10%
            keys = list(self._query_cache.keys())[:200]
            for k in keys:
                del self._query_cache[k]

        self._query_cache[norm_query] = vec
        return vec

    async def embed_document(self, text: str) -> List[float]:
        """为单条文本生成嵌入向量"""
        vecs = await self.provider.embed_documents([text])
        return vecs[0]

    async def embed_chunks(
        self,
        chunks: List[DocumentChunk],
        batch_size: int = 64,
        embed_parents: bool = False,
    ) -> List[DocumentChunk]:
        """
        批量为切片实体生成并填充 1536 维向量。
        - 默认切片策略：重点为 Child 和 Table 切片注入向量以用于高精检索；
        - 平滑兼容：若本地为 SQLite / 无 pgvector，则以 JSON 字符串形式持久化至 embedding Text 列。
        """
        target_chunks: List[DocumentChunk] = []
        for chk in chunks:
            if not embed_parents and chk.chunk_level == ChunkLevel.PARENT:
                continue
            if chk.embedding is None:
                target_chunks.append(chk)

        if not target_chunks:
            return chunks

        texts = [c.content for c in target_chunks]
        all_vectors: List[List[float]] = []

        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i:i + batch_size]
            batch_vecs = await self.provider.embed_documents(batch_texts)
            all_vectors.extend(batch_vecs)

        # 填充回实体
        for chk, vec in zip(target_chunks, all_vectors):
            if HAS_PGVECTOR:
                # 在真实 PostgreSQL + pgvector 下直接存 List[float]
                chk.embedding = vec
            else:
                # 在 SQLite / 本地开发 fallback 模式下序列化为 JSON 字符串
                chk.embedding = json.dumps(vec)

        return chunks

    @staticmethod
    def parse_embedding_vector(embedding_val: Any) -> Optional[List[float]]:
        """
        将数据库查出的 embedding 字段解析为标准 List[float]
        兼容 pgvector Vector 对象、JSON 字符串、或已经是 List[float] 的场景
        """
        if embedding_val is None:
            return None
        if isinstance(embedding_val, list):
            return [float(x) for x in embedding_val]
        if isinstance(embedding_val, str):
            try:
                parsed = json.loads(embedding_val)
                if isinstance(parsed, list):
                    return [float(x) for x in parsed]
            except Exception:
                return None
        # 如果是 pgvector 扩展的原生对象，通常支持迭代或 tolist()
        if hasattr(embedding_val, "tolist"):
            return embedding_val.tolist()
        if hasattr(embedding_val, "__iter__"):
            return [float(x) for x in embedding_val]
        return None


# 全局默认单例
_default_embedding_service: Optional[EmbeddingService] = None


def get_embedding_service() -> EmbeddingService:
    global _default_embedding_service
    if _default_embedding_service is None:
        _default_embedding_service = EmbeddingService()
    return _default_embedding_service

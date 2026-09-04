"""
生产级多租户 RAG 知识库与智能审查领域模型 (SQLAlchemy 2.0 Async)
支持:
1. 租户物理/逻辑硬隔离 (Tenant-ID 过滤与 PostgreSQL RLS 策略)
2. pgvector 向量索引 (HNSW vector_cosine_ops)
3. 父子层级切片 (Parent-Child Chunking)
4. 全格式文档元数据与结构化审查结果记录 (ReviewResult, AuditTask)
"""

import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum as SQLEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# 生产级 JSONB 变体: 在 PostgreSQL 下为 JSONB，在 SQLite/测试环境平滑降级为 JSON
JSON_VARIANT = JSON().with_variant(JSONB, "postgresql")

# 尝试导入 pgvector 类型；如本地/测试无 pgvector C 扩展则优雅降级为通用 Text 兼容占位
try:
    from pgvector.sqlalchemy import Vector
    HAS_PGVECTOR = True
except ImportError:
    HAS_PGVECTOR = False
    Vector = None  # type: ignore


def generate_uuid() -> str:
    """生成标准化 UUID 字符串主键"""
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# 枚举定义 (Enums)
# ---------------------------------------------------------------------------

class TenantStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class ChunkLevel(str, Enum):
    """切片层级: 满足父子层级切片架构与表格多视角"""
    PARENT = "parent"       # 父切片 (1024~2048 token 完整小节，提供完备上下文)
    CHILD = "child"         # 子切片 (128~256 token 原子命题，供高精向量检索)
    TABLE = "table"         # 表格切片 (包含 Markdown 与多视角 LLM 摘要)


class TaskType(str, Enum):
    """审查任务类型"""
    FORMAT_STYLE = "format_style"            # 格式排版与断层质检
    CONSISTENCY = "consistency"              # 长文档数据前后一致性校验
    BID_COMPARISON = "bid_comparison"        # 招投标文件偏离度智能比对
    DUAL_AGENT_GENERATION = "dual_agent_gen" # 双智能体闭环方案生成与校核
    RISK_WARNING = "risk_warning"            # 历史经验与风险主动拦截


class TaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"
    HUMAN_REVIEW = "human_review"            # 等待人工介入审核


class DeviationType(str, Enum):
    """招投标对齐偏离度分类"""
    FULL_COMPLIANCE = "full_compliance"      # 完全满足
    MISSING = "missing"                      # 缺失项 (未响应)
    POSITIVE = "positive"                    # 正偏离 (优于招标要求)
    NEGATIVE = "negative"                    # 负偏离 (实质性不满足，重大风险)
    NOT_APPLICABLE = "not_applicable"        # 不适用


class SeverityLevel(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"                    # 严重冲突/废标风险项


# ---------------------------------------------------------------------------
# 1. 租户实体 (Tenant)
# ---------------------------------------------------------------------------

class Tenant(Base):
    """
    企业级租户实体表
    所有业务数据均在此租户边界下硬隔离
    """
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False, comment="租户唯一编码")
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="企业/组织全称")
    status: Mapped[TenantStatus] = mapped_column(
        SQLEnum(TenantStatus), default=TenantStatus.ACTIVE, nullable=False, index=True
    )
    config: Mapped[Dict[str, Any]] = mapped_column(
        JSON_VARIANT,
        default=dict,
        nullable=False,
        comment="租户专属配置(存储桶配额、模型路由、质检规则阈值等)"
    )
    
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # 关联关系
    documents: Mapped[List["Document"]] = relationship("Document", back_populates="tenant", cascade="all, delete-orphan")
    audit_tasks: Mapped[List["AuditTask"]] = relationship("AuditTask", back_populates="tenant", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# 2. 文档实体 (Document)
# ---------------------------------------------------------------------------

class Document(Base):
    """
    文档元数据实体
    支持多种异构文件(DOCX, PDF, OFD, XLSX, PPTX, MPP, CAD)
    存储 MinIO 对象路径与 AST 解析快照
    """
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[str] = mapped_column(String(64), default="default", nullable=False, index=True)

    title: Mapped[str] = mapped_column(String(512), nullable=False, comment="文件原始名称/标题")
    file_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="文件后缀/类型: docx,pdf,ofd,xlsx,mpp,cad...")
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    
    # 租户隔离规范存储路径: /{tenant_id}/{workspace_id}/{file_hash}.{ext}
    s3_path: Mapped[str] = mapped_column(String(1024), nullable=False, comment="对象存储物理路径")
    file_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False, comment="SHA256 去重哈希")

    # 文档状态与结构化 AST 快照
    parse_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, comment="解析状态: pending/parsing/success/failed")
    doc_ast: Mapped[Dict[str, Any]] = mapped_column(
        JSON_VARIANT, default=dict, nullable=False, comment="标准化 Document AST 语法树结构"
    )
    doc_metadata: Mapped[Dict[str, Any]] = mapped_column(
        JSON_VARIANT, default=dict, nullable=False, comment="多维标签(专业领域、风险等级、项目阶段等)"
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # 关联
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="documents")
    chunks: Mapped[List["DocumentChunk"]] = relationship("DocumentChunk", back_populates="document", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_doc_tenant_workspace", "tenant_id", "workspace_id"),
        Index("idx_doc_tenant_file_hash", "tenant_id", "file_hash"),
    )


# ---------------------------------------------------------------------------
# 3. 文档切片实体 (DocumentChunk) - 含父子切片与 pgvector 向量列
# ---------------------------------------------------------------------------

class DocumentChunk(Base):
    """
    文档切片实体
    支持:
    - 父子层级切片 (Parent-Child Chunking): parent_chunk_id 关联
    - 表格多视角摘要 (Table Multi-representation)
    - pgvector 1536 维向量与 HNSW 余弦索引
    - 租户硬隔离复合索引
    """
    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # 父子切片自关联
    parent_chunk_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("document_chunks.id", ondelete="SET NULL"), nullable=True, index=True,
        comment="父切片ID(如当前为子切片，则指向父级大章节切片)"
    )
    chunk_level: Mapped[ChunkLevel] = mapped_column(
        SQLEnum(ChunkLevel), default=ChunkLevel.CHILD, nullable=False, index=True
    )

    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, comment="文档内分块序列号")
    section_path: Mapped[str] = mapped_column(String(1024), default="", nullable=False, comment="大纲路径面包屑，如: 第1章/1.2节")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="切片纯文本内容或表格 Markdown")
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # pgvector 稠密向量 (1536 维标准嵌入，在 SQLite/本地测试平滑降级为 Text)
    if HAS_PGVECTOR and Vector is not None:
        embedding: Mapped[Optional[Any]] = mapped_column(
            Vector(1536).with_variant(Text(), "sqlite"),
            nullable=True,
            comment="1536维文本嵌入向量"
        )
    else:
        # Fallback 兼容层
        embedding: Mapped[Optional[str]] = mapped_column(
            Text, nullable=True, comment="向量数据(Base64或JSON浮点数组)"
        )

    # 定位与多视角元数据
    page_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="对应物理页码/Sheet")
    bbox_coordinates: Mapped[Dict[str, Any]] = mapped_column(
        JSON_VARIANT, default=dict, nullable=False, comment="定位坐标 [x0, y0, x1, y1]"
    )
    chunk_metadata: Mapped[Dict[str, Any]] = mapped_column(
        JSON_VARIANT, default=dict, nullable=False, comment="包含表格结构摘要、命题提取、实体标签等"
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # 关联
    document: Mapped["Document"] = relationship("Document", back_populates="chunks")
    parent_chunk: Mapped[Optional["DocumentChunk"]] = relationship(
        "DocumentChunk", remote_side=[id], backref="child_chunks"
    )

    @property
    def is_table_isolated(self) -> bool:
        """是否为原子隔离的表格切片"""
        return self.chunk_level == ChunkLevel.TABLE or bool(self.chunk_metadata.get("is_table_isolated", False))

    @property
    def chunk_id(self) -> str:
        """兼容 chunk_id 属性访问"""
        return self.id

    @property
    def page_or_sheet(self) -> Optional[str]:
        """兼容页面/工作表名称访问"""
        if "page_or_sheet" in self.chunk_metadata:
            return str(self.chunk_metadata["page_or_sheet"])
        if self.page_number is not None:
            return str(self.page_number)
        return None

    __table_args__ = (
        Index("idx_chunk_tenant_doc", "tenant_id", "document_id", "chunk_index"),
        Index("idx_chunk_tenant_level", "tenant_id", "chunk_level"),
        Index(
            "idx_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"}
        ) if HAS_PGVECTOR else Index("idx_chunks_doc_mock", "tenant_id", "document_id"),
    )


# ---------------------------------------------------------------------------
# 4. 审查比对任务实体 (AuditTask)
# ---------------------------------------------------------------------------

class AuditTask(Base):
    """
    审查与比对任务主表
    记录审查执行状态、配置、汇总与审计日志
    """
    __tablename__ = "audit_tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_type: Mapped[TaskType] = mapped_column(SQLEnum(TaskType), nullable=False, index=True)
    status: Mapped[TaskStatus] = mapped_column(
        SQLEnum(TaskStatus), default=TaskStatus.PENDING, nullable=False, index=True
    )

    # 关联文档 (支持单文档自身质检，或主客文档比对，如自编标书 vs 招标文件)
    source_document_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True,
        comment="待审文档ID (如自编投标文件)"
    )
    target_document_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True,
        comment="基准对照文档ID (如招标文件/规范评分表)"
    )

    task_config: Mapped[Dict[str, Any]] = mapped_column(
        JSON_VARIANT, default=dict, nullable=False, comment="任务入参、规则阈值、模型参数"
    )
    summary_report: Mapped[Dict[str, Any]] = mapped_column(
        JSON_VARIANT, default=dict, nullable=False, comment="综合审计报告与统计度量"
    )
    total_issues_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    high_risk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # 关联
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="audit_tasks")
    review_results: Mapped[List["ReviewResult"]] = relationship(
        "ReviewResult", back_populates="task", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_audit_tenant_status", "tenant_id", "status"),
        Index("idx_audit_tenant_type", "tenant_id", "task_type"),
    )


# ---------------------------------------------------------------------------
# 5. 审查比对细项结果 (ReviewResult)
# ---------------------------------------------------------------------------

class ReviewResult(Base):
    """
    审查明细与偏离度比对结果表
    每一项明确指出风险严重程度、偏离类型、置信度以及自编文档的具体锚点
    """
    __tablename__ = "review_results"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("audit_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # 偏离度与风险级别
    deviation_type: Mapped[DeviationType] = mapped_column(
        SQLEnum(DeviationType), default=DeviationType.NOT_APPLICABLE, nullable=False, index=True,
        comment="完全满足 / 缺失项 / 正偏离 / 负偏离"
    )
    severity: Mapped[SeverityLevel] = mapped_column(
        SQLEnum(SeverityLevel), default=SeverityLevel.LOW, nullable=False, index=True
    )
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False, comment="置信度 [0.0 ~ 1.0]")

    # 条款与审查信息
    rule_category: Mapped[str] = mapped_column(String(128), default="general", nullable=False, comment="规则类别(如: 工期一致性/技术标评分)")
    title: Mapped[str] = mapped_column(String(512), nullable=False, comment="问题标题/评分指标名称")
    description: Mapped[str] = mapped_column(Text, nullable=False, comment="差异描述/审查意见")
    suggestion: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="智能修订意见或补正建议")

    # 原文锚点与证据链 (精准反查页码与段落)
    source_section: Mapped[str] = mapped_column(String(512), default="", nullable=False, comment="自编标书章节定位")
    source_page: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="自编标书页码")
    source_quote: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="自编标书原文引述")

    benchmark_section: Mapped[str] = mapped_column(String(512), default="", nullable=False, comment="招标文件/规范章节")
    benchmark_quote: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="招标文件标准要求原文")

    # 差异载荷 (如前后数值矛盾的键值对)
    diff_payload: Mapped[Dict[str, Any]] = mapped_column(
        JSON_VARIANT, default=dict, nullable=False, comment="结构化差异对比数据"
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    task: Mapped["AuditTask"] = relationship("AuditTask", back_populates="review_results")

    __table_args__ = (
        Index("idx_review_tenant_task", "tenant_id", "task_id"),
        Index("idx_review_tenant_severity", "tenant_id", "severity"),
        Index("idx_review_tenant_deviation", "tenant_id", "deviation_type"),
    )


# ---------------------------------------------------------------------------
# 6. 历史工程审查风险与经验知识库实体 (HistoricalAuditRisk) - Feature 29
# ---------------------------------------------------------------------------

class HistoricalAuditRisk(Base):
    """
    历史工程审查风险知识库实体
    存储历史工程项目审计失败表单、缺陷条款、合同纠纷案件及重大处罚记录
    支持:
    1. 租户物理/逻辑硬隔离 (tenant_id 与 PostgreSQL 16+ RLS)
    2. pgvector 1536 维向量余弦索引 (HNSW)
    3. 多维度工程特征标签与数值阈值触发检索
    4. 预防性系统提示词护栏 (preventive_guardrail_prompt)
    """
    __tablename__ = "historical_audit_risks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True,
        comment="租户唯一标识 (硬隔离边界)"
    )

    # 业务分类与工程属性
    project_type: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True,
        comment="工程大类: 房建, 市政, 弱电智能化, 轨道交通, 水利水电, 公路桥梁..."
    )
    risk_category: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True,
        comment="风险类别: 工期延误, 造价超概, 安全基坑坍塌, 环保违规, 资质造假, 质量通病, 合同纠纷..."
    )
    risk_title: Mapped[str] = mapped_column(
        String(512), nullable=False, comment="风险条目标题"
    )
    severity: Mapped[SeverityLevel] = mapped_column(
        SQLEnum(SeverityLevel), default=SeverityLevel.HIGH, nullable=False, index=True,
        comment="风险严重等级: CRITICAL / HIGH / MEDIUM / LOW"
    )

    # 案例描述与经验教训
    defect_description: Mapped[str] = mapped_column(
        Text, nullable=False, comment="历史工程失误描述、缺陷条款、合同纠纷记录或行政处罚案情"
    )
    lesson_learned: Mapped[str] = mapped_column(
        Text, nullable=False, comment="历史教训与复盘总结，阐明根因与治理经验"
    )
    preventive_guardrail_prompt: Mapped[str] = mapped_column(
        Text, nullable=False, comment="预防性防护约束提示词，用于直接注入 Generator Agent 的系统提示词中"
    )

    # 标签与匹配规则属性
    tags: Mapped[List[str]] = mapped_column(
        JSON_VARIANT, default=list, nullable=False,
        comment="规则标签列表: ['深基坑', '开挖深度>5m', '危大工程', '雨季施工', '超概算', '一级建造师']"
    )
    rule_conditions: Mapped[Dict[str, Any]] = mapped_column(
        JSON_VARIANT, default=dict, nullable=False,
        comment="结构化参数触发阈值: {'min_excavation_depth': 5.0, 'max_duration_days': 120}"
    )

    # 历史案卷溯源数据 (审计追踪与商业智能)
    source_case_id: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, comment="关联历史案卷/工程项目编号，如 'PRJ-2024-SZ-041'"
    )
    source_project_name: Mapped[Optional[str]] = mapped_column(
        String(512), nullable=True, comment="原工程名称，如 '某市轨道交通三期深基坑工程'"
    )
    financial_loss_cny: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, comment="经济损失/违约索赔/行政处罚金额 (万元)"
    )
    delay_days: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="造成的工期延误天数"
    )

    # pgvector 1536 维向量列 (兼容 SQLite 文本存储)
    if HAS_PGVECTOR and Vector is not None:
        embedding: Mapped[Optional[Any]] = mapped_column(
            Vector(1536).with_variant(Text(), "sqlite"),
            nullable=True,
            comment="1536维文本嵌入向量"
        )
    else:
        embedding: Mapped[Optional[str]] = mapped_column(
            Text, nullable=True, comment="向量数据(Base64或JSON浮点数组)"
        )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # 关系映射
    tenant: Mapped["Tenant"] = relationship("Tenant")

    __table_args__ = (
        Index("idx_hist_risk_tenant_proj", "tenant_id", "project_type"),
        Index("idx_hist_risk_tenant_cat", "tenant_id", "risk_category"),
        Index("idx_hist_risk_tenant_severity", "tenant_id", "severity"),
        Index(
            "idx_hist_risk_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"}
        ) if HAS_PGVECTOR and Vector is not None else Index("idx_hist_risk_mock", "tenant_id", "project_type"),
    )


# ---------------------------------------------------------------------------
# 7. PostgreSQL 行级安全策略 (Row-Level Security / RLS) DDL 生成脚本
# ---------------------------------------------------------------------------

def generate_rls_sql(tables: Optional[List[str]] = None) -> str:
    """
    生成针对 PostgreSQL 16+ 的生产级 RLS 行级安全加固 DDL 脚本
    采用统一设置 Session 级当前租户 app.current_tenant_id 机制
    核心安全特性:
    1. ENABLE ROW LEVEL SECURITY: 开启行级安全策略。
    2. FORCE ROW LEVEL SECURITY: 强制表拥有者 (Table Owner / App DB User) 同样受到 RLS 约束，杜绝越权。
    3. NULLIF(current_setting('app.current_tenant_id', true), ''): 租户未设置时安全求值为 NULL，禁止返回任何数据。
    """
    target_tables = tables or ["documents", "document_chunks", "audit_tasks", "review_results", "historical_audit_risks"]
    ddl_statements = [
        "-- 启用 PostgreSQL 行级安全 (Row-Level Security)",
        "CREATE EXTENSION IF NOT EXISTS vector;",
    ]
    for tbl in target_tables:
        ddl_statements.extend([
            f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY;",
            f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY;",
            f"DROP POLICY IF EXISTS tenant_isolation_policy ON {tbl};",
            f"CREATE POLICY tenant_isolation_policy ON {tbl}",
            f"    FOR ALL",
            f"    USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), ''))",
            f"    WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), ''));",
        ])
    return "\n".join(ddl_statements)

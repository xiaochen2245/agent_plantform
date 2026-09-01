"""UploadFile 模型：员工上传的对话附件（契约 v4）。

- id 形如 f_<16 hex>（路由生成），对外即 file_id
- 本地卷存储（MVP；MinIO/S3 二期），storage_path 为相对后端 cwd 的路径
- 发送时才转发 Dify /files/upload 换 dify_file_id（orchestrator 批准的实现细节）
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UploadFile(Base):
    __tablename__ = "upload_files"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # f_<hex>
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)  # 清洗后的展示名
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime: Mapped[str] = mapped_column(String(100), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

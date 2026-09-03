"""rag_audit_logs 表（#28：RAGFlow 写操作审计，KbAuditLog 同形状）。

Revision ID: b3e7f1a5c9d2
Revises: a8f1c4d9e2b7
Create Date: 2026-09-05 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b3e7f1a5c9d2"
down_revision: Union[str, Sequence[str], None] = "a8f1c4d9e2b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rag_audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    op.create_index("ix_rag_audit_logs_user_id", "rag_audit_logs", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_rag_audit_logs_user_id", table_name="rag_audit_logs")
    op.drop_table("rag_audit_logs")

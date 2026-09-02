"""kb_audit_logs 表（契约 v9：知识库写操作审计）。

Revision ID: f5d3a2c1b7e6
Revises: e3b1f0a9c2d4
Create Date: 2026-09-02 17:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f5d3a2c1b7e6"
down_revision: Union[str, Sequence[str], None] = "e3b1f0a9c2d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "kb_audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    op.create_index("ix_kb_audit_logs_user_id", "kb_audit_logs", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_kb_audit_logs_user_id", table_name="kb_audit_logs")
    op.drop_table("kb_audit_logs")

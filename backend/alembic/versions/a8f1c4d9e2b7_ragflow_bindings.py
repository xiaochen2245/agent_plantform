"""ragflow_bindings 表（W2 租户映射：部门 → RAGFlow 影子账号）。

Revision ID: a8f1c4d9e2b7
Revises: c7e9a10b3f2d
Create Date: 2026-09-04 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a8f1c4d9e2b7"
down_revision: Union[str, Sequence[str], None] = "f5d3a2c1b7e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ragflow_bindings",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer, "sqlite"),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column(
            "department_id",
            sa.BigInteger().with_variant(sa.Integer, "sqlite"),
            nullable=False,
            unique=True,
        ),
        sa.Column("ragflow_email", sa.String(255), nullable=False, unique=True),
        sa.Column("ragflow_password_enc", sa.String(512), nullable=False),
        sa.Column("ragflow_api_token_enc", sa.String(512), nullable=False),
        sa.Column("default_dataset_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ragflow_bindings_department_id", "ragflow_bindings", ["department_id"])


def downgrade() -> None:
    op.drop_index("ix_ragflow_bindings_department_id", table_name="ragflow_bindings")
    op.drop_table("ragflow_bindings")

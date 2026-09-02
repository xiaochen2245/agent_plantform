"""dataset_authorizations 表（契约 v8：知识库租户隔离）。

Revision ID: e3b1f0a9c2d4
Revises: c7e9a10b3f2d
Create Date: 2026-09-02 15:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e3b1f0a9c2d4"
down_revision: Union[str, Sequence[str], None] = "c7e9a10b3f2d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dataset_authorizations",
        sa.Column("dataset_id", sa.String(length=64), primary_key=True),
        sa.Column("principal_type", sa.String(length=10), primary_key=True),
        sa.Column("principal_id", sa.Integer(), primary_key=True),
        comment="dataset_id + (principal_type, principal_id) 复合主键",
    )


def downgrade() -> None:
    op.drop_table("dataset_authorizations")

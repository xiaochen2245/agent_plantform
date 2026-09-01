"""messages.reasoning 可空列（契约 v6：思考过程持久化）。

Revision ID: c7e9a10b3f2d
Revises: a41f2c7d90b1
Create Date: 2026-09-01 20:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c7e9a10b3f2d"
down_revision: Union[str, Sequence[str], None] = "a41f2c7d90b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("reasoning", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "reasoning")

"""add published_at to hotels

Revision ID: add_hotel_published_at
Revises: users_extra_fields
Create Date: 2026-05-17 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "add_hotel_published_at"
down_revision: Union[str, None] = "users_extra_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "hotels",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Backfill: hotels currently `published` get published_at = updated_at as a
    # best-effort approximation of when they were last published.
    op.execute(
        "UPDATE hotels SET published_at = updated_at WHERE status = 'published'"
    )


def downgrade() -> None:
    op.drop_column("hotels", "published_at")

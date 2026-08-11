"""Add clients.photo_url_source — TBB-53 (TG avatar refresh marker).

Revision ID: client_photo_url_source
Revises: drop_multilang_columns
Create Date: 2026-08-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "client_photo_url_source"
down_revision: Union[str, None] = "drop_multilang_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "clients",
        sa.Column("photo_url_source", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("clients", "photo_url_source")

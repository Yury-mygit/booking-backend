"""webhook_idempotency table — DP-5 idempotency-key storage

Revision ID: webhook_idempotency
Revises: payment_status_cancelled
Create Date: 2026-08-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "webhook_idempotency"
down_revision: Union[str, None] = "payment_status_cancelled"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "webhook_idempotency",
        sa.Column("key", sa.String(length=128), primary_key=True),
        sa.Column("endpoint", sa.String(length=64), nullable=False),
        sa.Column("response_body", postgresql.JSONB(), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=False, server_default="200"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_webhook_idempotency_expires_at", "webhook_idempotency", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_webhook_idempotency_expires_at", table_name="webhook_idempotency")
    op.drop_table("webhook_idempotency")

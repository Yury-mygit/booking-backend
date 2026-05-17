"""partner_staff + audit_log

Revision ID: partner_staff_audit
Revises: add_hotel_published_at
Create Date: 2026-05-17 18:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "partner_staff_audit"
down_revision: Union[str, None] = "add_hotel_published_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "partner_staff",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "staff_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "perm_manage_hotel",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "perm_manage_rooms",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "perm_manage_bookings",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "perm_manage_staff",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("note", sa.String(128)),
        sa.Column(
            "added_by_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("owner_user_id", "staff_user_id", name="uq_partner_staff_owner_staff"),
    )
    op.create_index(
        "ix_partner_staff_staff", "partner_staff", ["staff_user_id"]
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("actor_role", sa.String(16), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("subject_type", sa.String(32)),
        sa.Column("subject_id", sa.Integer),
        sa.Column("payload", JSONB),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_audit_action", "audit_log", ["action"])
    op.create_index(
        "ix_audit_owner_created",
        "audit_log",
        ["owner_user_id", sa.text("created_at DESC")],
    )
    op.create_index("ix_audit_subject", "audit_log", ["subject_type", "subject_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_subject", table_name="audit_log")
    op.drop_index("ix_audit_owner_created", table_name="audit_log")
    op.drop_index("ix_audit_action", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("ix_partner_staff_staff", table_name="partner_staff")
    op.drop_table("partner_staff")

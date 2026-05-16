"""clients table + bookings.client_id (drops bookings.user_id)

Revision ID: add_clients_table
Revises: 06f4e101d437
Create Date: 2026-05-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "add_clients_table"
down_revision: Union[str, None] = "06f4e101d437"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    doc_kind = postgresql.ENUM(
        "passport", "id_card", "driving_license", "other", name="doc_kind"
    )
    doc_kind.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "clients",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("first_name", sa.String(128), nullable=False),
        sa.Column("last_name", sa.String(128), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("email", sa.String(256), nullable=True),
        sa.Column("doc_kind",
                  postgresql.ENUM("passport", "id_card", "driving_license", "other",
                                  name="doc_kind", create_type=False),
                  nullable=True),
        sa.Column("doc_number", sa.String(64), nullable=True),
        sa.Column("photo_url", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_clients_user_id", "clients", ["user_id"])
    op.create_index("ix_clients_phone", "clients", ["phone"])
    op.create_index("ix_clients_email", "clients", ["email"])

    # 2. Add bookings.client_id (nullable for now).
    op.add_column("bookings", sa.Column("client_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "bookings_client_id_fkey",
        "bookings", "clients",
        ["client_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_index("ix_bookings_client_id", "bookings", ["client_id"])

    # 3. Backfill: one client per booking-user (reuse if already created).
    #    For every distinct user_id referenced by bookings, create a clients row
    #    (if there isn't one with that user_id already) using the user's first_name,
    #    then point bookings.client_id at it.
    op.execute(
        """
        INSERT INTO clients (user_id, first_name)
        SELECT DISTINCT b.user_id, COALESCE(u.first_name, 'Client')
        FROM bookings b
        JOIN users u ON u.id = b.user_id
        WHERE NOT EXISTS (SELECT 1 FROM clients c WHERE c.user_id = b.user_id)
        """
    )
    op.execute(
        """
        UPDATE bookings b
        SET client_id = c.id
        FROM clients c
        WHERE c.user_id = b.user_id
        """
    )

    # 4. Tighten: client_id NOT NULL, drop user_id.
    op.alter_column("bookings", "client_id", nullable=False)
    op.drop_index("ix_bookings_user_id", table_name="bookings")
    op.drop_constraint("bookings_user_id_fkey", "bookings", type_="foreignkey")
    op.drop_column("bookings", "user_id")


def downgrade() -> None:
    # Re-add bookings.user_id, backfill from clients.user_id (some may be NULL → fail).
    op.add_column("bookings", sa.Column("user_id", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE bookings b
        SET user_id = c.user_id
        FROM clients c
        WHERE c.id = b.client_id
        """
    )
    op.alter_column("bookings", "user_id", nullable=False)
    op.create_foreign_key(
        "bookings_user_id_fkey", "bookings", "users",
        ["user_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_index("ix_bookings_user_id", "bookings", ["user_id"])

    op.drop_index("ix_bookings_client_id", table_name="bookings")
    op.drop_constraint("bookings_client_id_fkey", "bookings", type_="foreignkey")
    op.drop_column("bookings", "client_id")

    op.drop_index("ix_clients_email", table_name="clients")
    op.drop_index("ix_clients_phone", table_name="clients")
    op.drop_index("ix_clients_user_id", table_name="clients")
    op.drop_table("clients")

    postgresql.ENUM(name="doc_kind").drop(op.get_bind(), checkfirst=True)

"""users: last_name, username, email

Revision ID: users_extra_fields
Revises: add_clients_table
Create Date: 2026-05-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "users_extra_fields"
down_revision: Union[str, None] = "add_clients_table"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_name", sa.String(128), nullable=True))
    op.add_column("users", sa.Column("username", sa.String(64), nullable=True))
    op.add_column("users", sa.Column("email", sa.String(256), nullable=True))
    op.create_index("ix_users_username", "users", ["username"])


def downgrade() -> None:
    op.drop_index("ix_users_username", table_name="users")
    op.drop_column("users", "email")
    op.drop_column("users", "username")
    op.drop_column("users", "last_name")

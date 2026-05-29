"""users.bot_blocked_or_unreachable

Карта: open_cards/cards/booking/feature/2026-05-28-client-hotel-chat.md
(этап 4.3). Флаг ставится в True когда TG sendMessage возвращает
403 (бот заблокирован или /start не нажат), сбрасывается в False при
любом успешном sendMessage. SPA читает поле и показывает баннер
«нажмите Start у @rforge_stay_bot».

Revision ID: users_bot_blocked
Revises: chat_threads_messages
Create Date: 2026-05-30 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "users_bot_blocked"
down_revision: Union[str, None] = "chat_threads_messages"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "bot_blocked_or_unreachable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "bot_blocked_or_unreachable")

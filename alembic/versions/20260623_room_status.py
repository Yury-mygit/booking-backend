"""rooms.status (draft/published/blocked)

Карта: cards/booking/feature/2026-06-23-room-publish-toggle.md.

- Создаём enum `room_status` (draft|published|blocked) — copy of
  hotel_status, чтобы не реюзать чужой тип.
- rooms.status NOT NULL DEFAULT 'published' — операционная
  необходимость (prod-комнаты не должны исчезнуть после деплоя).
- Новые комнаты партнёр создаёт явно через POST /p/hotels/{id}/rooms
  с status='draft' — это задаёт API-слой, не миграция.

Revision ID: room_status
Revises: amenities
Create Date: 2026-06-23 10:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "room_status"
down_revision: Union[str, None] = "amenities"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    room_status = postgresql.ENUM(
        "draft", "published", "blocked", name="room_status"
    )
    room_status.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "rooms",
        sa.Column(
            "status",
            postgresql.ENUM(
                "draft", "published", "blocked", name="room_status",
                create_type=False,
            ),
            server_default="published",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("rooms", "status")
    postgresql.ENUM(name="room_status").drop(op.get_bind(), checkfirst=True)

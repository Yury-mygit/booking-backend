"""staff role per-hotel scope + audit hotel_id

Карта: cards/booking/feature/2026-06-27-12-staff-per-hotel-rights.md (#12).

Изменения:
  1. partner_staff_role: drop composite PK (staff_id, role_id) →
     surrogate id BigInteger PK. Add hotel_id INT FK NULL ON DELETE
     RESTRICT; removed_at DateTime(tz) NULL; created_at DateTime(tz)
     NOT NULL DEFAULT now(). Partial unique
     (staff_id, role_id, hotel_id) NULLS NOT DISTINCT WHERE removed_at
     IS NULL.
  2. β-разворот: для каждой существующей psr-строки (с NULL hotel_id)
     — INSERT N новых строк по числу отелей партнёра, затем soft-delete
     оригинала. Атомарно через CTE; идемпотентно.
  3. audit_log: add hotel_id INT FK NULL ON DELETE SET NULL.
     Backfill: subject_type='hotel' → hotel_id=subject_id;
     subject_type='room' → JOIN rooms.hotel_id; остальное NULL.

Revision ID: staff_role_per_hotel_scope
Revises: partner_staff_name_fields
Create Date: 2026-06-27
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "staff_role_per_hotel_scope"
down_revision: Union[str, None] = "partner_staff_name_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Расширяем partner_staff_role.
    # 1a. Drop composite PK (имя по умолчанию = partner_staff_role_pkey).
    op.execute("ALTER TABLE partner_staff_role DROP CONSTRAINT partner_staff_role_pkey")

    # 1b. surrogate id.
    op.add_column(
        "partner_staff_role",
        sa.Column("id", sa.BigInteger(), nullable=True),
    )
    op.execute("CREATE SEQUENCE IF NOT EXISTS partner_staff_role_id_seq OWNED BY partner_staff_role.id")
    op.execute("UPDATE partner_staff_role SET id = nextval('partner_staff_role_id_seq') WHERE id IS NULL")
    op.alter_column("partner_staff_role", "id", nullable=False)
    op.execute("ALTER TABLE partner_staff_role ALTER COLUMN id SET DEFAULT nextval('partner_staff_role_id_seq')")
    op.create_primary_key("partner_staff_role_pkey", "partner_staff_role", ["id"])

    # 1c. hotel_id (nullable временно — заполнится в β-развороте).
    op.add_column(
        "partner_staff_role",
        sa.Column(
            "hotel_id", sa.Integer(),
            sa.ForeignKey("hotels.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_partner_staff_role_hotel_id",
        "partner_staff_role", ["hotel_id"],
    )

    # 1d. removed_at + created_at.
    op.add_column(
        "partner_staff_role",
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "partner_staff_role",
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )

    # 1e. Индексы по FK-колонкам (PK на staff_id+role_id больше нет).
    op.create_index(
        "ix_partner_staff_role_staff_id",
        "partner_staff_role", ["staff_id"],
    )
    op.create_index(
        "ix_partner_staff_role_role_id",
        "partner_staff_role", ["role_id"],
    )

    # 1f. Partial unique с NULLS NOT DISTINCT (PG15+; db_shared = PG16).
    op.execute(
        """
        CREATE UNIQUE INDEX uq_psr_staff_role_hotel_active
        ON partner_staff_role (staff_id, role_id, hotel_id)
        NULLS NOT DISTINCT
        WHERE removed_at IS NULL
        """
    )

    # 2. β-разворот: NULL hotel_id → N строк по числу отелей партнёра.
    # Атомарно через CTE. Идемпотентно: на повторном запуске originals
    # будет пустой (все уже softdeleted), INSERT/UPDATE no-op.
    op.execute(
        """
        WITH originals AS (
          SELECT psr.id, psr.staff_id, psr.role_id, ps.owner_user_id
          FROM partner_staff_role psr
          JOIN partner_staff ps ON ps.id = psr.staff_id
          WHERE psr.hotel_id IS NULL AND psr.removed_at IS NULL
        ),
        inserted AS (
          INSERT INTO partner_staff_role (staff_id, role_id, hotel_id, created_at)
          SELECT o.staff_id, o.role_id, h.id, now()
          FROM originals o
          JOIN hotels h ON h.owner_user_id = o.owner_user_id
          RETURNING 1
        )
        UPDATE partner_staff_role
        SET removed_at = now()
        WHERE id IN (SELECT id FROM originals)
        """
    )

    # 3. audit_log.hotel_id + backfill.
    op.add_column(
        "audit_log",
        sa.Column(
            "hotel_id", sa.Integer(),
            sa.ForeignKey("hotels.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_audit_log_hotel_id",
        "audit_log", ["hotel_id"],
    )

    # 3a. Backfill: subject_type='hotel' → hotel_id = subject_id (если
    # такой hotel ещё существует).
    op.execute(
        """
        UPDATE audit_log a
        SET hotel_id = a.subject_id
        FROM hotels h
        WHERE a.subject_type = 'hotel'
          AND h.id = a.subject_id
          AND a.hotel_id IS NULL
        """
    )

    # 3b. Backfill: subject_type='room' → hotel_id из rooms.
    op.execute(
        """
        UPDATE audit_log a
        SET hotel_id = r.hotel_id
        FROM rooms r
        WHERE a.subject_type = 'room'
          AND r.id = a.subject_id
          AND a.hotel_id IS NULL
        """
    )


def downgrade() -> None:
    # 3. audit_log.hotel_id — drop.
    op.drop_index("ix_audit_log_hotel_id", table_name="audit_log")
    op.drop_column("audit_log", "hotel_id")

    # 2. β-collapse: схлопнуть N строк обратно в одну NULL.
    # Восстанавливаем оригинальные softdeleted (где hotel_id IS NULL,
    # removed_at IS NOT NULL); все hotel-scoped дочерние строки удаляем.
    op.execute("UPDATE partner_staff_role SET removed_at = NULL WHERE hotel_id IS NULL")
    op.execute("DELETE FROM partner_staff_role WHERE hotel_id IS NOT NULL")

    # 1f-1c reverse.
    op.execute("DROP INDEX IF EXISTS uq_psr_staff_role_hotel_active")
    op.drop_index("ix_partner_staff_role_role_id", table_name="partner_staff_role")
    op.drop_index("ix_partner_staff_role_staff_id", table_name="partner_staff_role")
    op.drop_column("partner_staff_role", "created_at")
    op.drop_column("partner_staff_role", "removed_at")
    op.drop_index("ix_partner_staff_role_hotel_id", table_name="partner_staff_role")
    op.drop_column("partner_staff_role", "hotel_id")

    # 1b reverse: drop surrogate PK, восстановить composite.
    op.execute("ALTER TABLE partner_staff_role DROP CONSTRAINT partner_staff_role_pkey")
    op.drop_column("partner_staff_role", "id")
    op.execute("DROP SEQUENCE IF EXISTS partner_staff_role_id_seq")
    op.create_primary_key(
        "partner_staff_role_pkey",
        "partner_staff_role", ["staff_id", "role_id"],
    )

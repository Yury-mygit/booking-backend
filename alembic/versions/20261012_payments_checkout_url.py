"""payments.checkout_url — повторная выдача той же страницы оплаты

`pay/init` при живом pending-платеже возвращает его checkout вместо нового
intent'а у PSP: иначе у брони одновременно открыты две страницы оплаты и
клиент может заплатить дважды. URL PSP отдаёт только при создании intent'а,
поэтому храним его в платеже.

Nullable: у старых платежей URL неизвестен — такие не переиспользуются.

Revision ID: payments_checkout_url
Revises: destinations_catalog
Create Date: 2026-10-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "payments_checkout_url"
down_revision: Union[str, None] = "destinations_catalog"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("payments", sa.Column("checkout_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("payments", "checkout_url")

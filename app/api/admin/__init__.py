"""Admin endpoints (/admin/*, require_role(UserRole.admin)).

Split-out по поддоменам (см. карту #49 этап 3, 2026-06-01):
- users    — list + verify-partner / promote-admin / revoke-partner / demote-admin
- hotels   — list + set-status
- bookings — list + cancel
- metrics  — global counters

Префикс `/admin` и tag `admin` навешиваются здесь — sub-router'ы
регистрируют чистые пути типа `/users`, `/hotels/{id}/status` и т.д.
"""
from fastapi import APIRouter

from app.api.admin import bookings, hotels, metrics, users

router = APIRouter(prefix="/admin", tags=["admin"])
router.include_router(users.router)
router.include_router(hotels.router)
router.include_router(bookings.router)
router.include_router(metrics.router)

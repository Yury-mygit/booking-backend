"""Partner block (/p/*, require_partner_or_staff).

Большой по объёму домен (~1700 строк до разбиения 2026-05-26). Сейчас
поделён на 7 sub-модулей по доменам — каждый держит собственный
`APIRouter()` (без prefix); prefix `/p` ставится здесь.

Авторизация: `require_partner_or_staff` (alias `require_verified_partner`)
проверяет `accessible_owners`. Per-action permissions
(manage_hotel/rooms/bookings/staff) — через `scope.get_my_*(..., require_perm=…)`.

Все write-операции логируются в `audit_log` через `audit(...)` helper —
читается на `/p/audit`.
"""
from fastapi import APIRouter

from app.api.partner import (
    audit,
    bookings,
    clients,
    hotels,
    rooms,
    services,
    staff,
)

router = APIRouter(prefix="/p", tags=["partner"])
router.include_router(hotels.router)
router.include_router(rooms.router)
router.include_router(services.router)
router.include_router(bookings.router)
router.include_router(clients.router)
router.include_router(staff.router)
router.include_router(audit.router)

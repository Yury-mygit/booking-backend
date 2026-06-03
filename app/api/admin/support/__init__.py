"""Admin Support endpoints (/admin/support/*).

Префикс `/admin` ставится `admin/__init__.py`; здесь добавляем `/support`.
Все sub-router'ы регистрируют чистые пути (`/tickets`, `/agents`, ...).

Защита по слоям:
- tickets / tags / canned — `require_support_agent`
- agents (roster) / categories / settings — `require_superadmin`
- list categories — `require_support_agent` (агенты видят все, в т.ч. inactive)
"""

from fastapi import APIRouter

from app.api.admin.support import (
    agents,
    canned,
    categories,
    settings,
    tags,
    tickets,
)

router = APIRouter(prefix="/support", tags=["admin-support"])
router.include_router(tickets.router)
router.include_router(agents.router)
router.include_router(tags.router)
router.include_router(categories.router)
router.include_router(settings.router)
router.include_router(canned.router)

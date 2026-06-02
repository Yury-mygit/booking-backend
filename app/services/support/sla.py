"""SLA-расчёт для тикетов.

При создании тикета (и при смене priority) вызывается `compute_due()` —
возвращает (first_response_due_at, resolution_due_at) на базе текущих
SupportSettings + priority. Значения сохраняются на ticket'е для
быстрого фильтра «просроченные» partial-индексом.
"""

from datetime import datetime, timedelta, timezone

from app.models.support import SupportSettings, TicketPriority


def _h(settings: SupportSettings, kind: str, priority: TicketPriority) -> int:
    """kind: 'response' | 'resolution'."""
    field = f"sla_{kind}_{priority.value}_h"
    return int(getattr(settings, field))


def compute_due(
    priority: TicketPriority,
    settings: SupportSettings,
    base_at: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Возвращает (first_response_due_at, resolution_due_at)."""
    base = base_at or datetime.now(timezone.utc)
    resp_h = _h(settings, "response", priority)
    res_h = _h(settings, "resolution", priority)
    return base + timedelta(hours=resp_h), base + timedelta(hours=res_h)

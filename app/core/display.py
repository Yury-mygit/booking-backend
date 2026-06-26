"""Display helpers — собирают человекочитаемые имена из моделей.

`staff_display_name` — ФИО staff'а партнёра, собранное из собственных
полей `PartnerStaff.{last,first,middle}_name`. Если ни одно не задано —
fallback на `User.first_name` (TG-имя). Карта #136.
"""
from __future__ import annotations

from app.models.models import PartnerStaff, User


def staff_display_name(staff: PartnerStaff, user: User) -> str:
    """Формат «Фамилия Имя Отчество», skip null/empty. Fallback — TG-имя."""
    parts = [staff.last_name, staff.first_name, staff.middle_name]
    parts = [p.strip() for p in parts if p and p.strip()]
    if parts:
        return " ".join(parts)
    return user.first_name or ""

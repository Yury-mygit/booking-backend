from datetime import datetime

from pydantic import BaseModel


class AuditEntryView(BaseModel):
    id: int
    owner_user_id: int
    actor_user_id: int
    actor_display_name: str | None
    actor_role: str
    action: str
    subject_type: str | None
    subject_id: int | None
    payload: dict | None
    created_at: datetime

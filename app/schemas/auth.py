from datetime import datetime

from pydantic import BaseModel, Field

from app.models.models import Lang, UserRole


class AuthTgRequest(BaseModel):
    init_data: str = Field(min_length=1)


class AuthTgUser(BaseModel):
    id: int
    telegram_id: int
    role: UserRole
    lang: Lang
    first_name: str | None
    is_new: bool
    # Only meaningful when role == "partner": "pending" until an admin verifies,
    # then "verified". For other roles — null.
    partner_status: str | None = None


class AuthTgResponse(BaseModel):
    token: str
    expires_at: datetime
    user: AuthTgUser

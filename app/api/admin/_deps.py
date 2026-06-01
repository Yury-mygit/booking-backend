"""Shared dependencies для admin/* endpoints."""
from app.core.deps import require_role
from app.models.models import UserRole

admin_only = require_role(UserRole.admin)

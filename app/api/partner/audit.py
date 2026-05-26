"""Partner audit log read: /p/audit (paged JSON) + /p/audit.csv (stream)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import AuthContext, require_verified_partner
from app.services import scope
from app.models.models import (
    AuditLog,
    User,
)
from app.schemas.partner import (
    AuditEntryView,
)

router = APIRouter()  # prefix задан в partner/__init__.py


# ─── Audit log read ───────────────────────────────────────────────────────

def _audit_stmt_base(
    ctx: AuthContext,
    owner_id: int | None,
    action_filter: str | None,
    subject_type_filter: str | None,
    since: datetime | None,
    until: datetime | None,
    q: str | None,
    actor_user_id: int | None,
):
    accessible_ids = scope.scope_owner_ids(ctx, owner_id)
    stmt = (
        select(AuditLog, User)
        .join(User, User.id == AuditLog.actor_user_id)
        .where(AuditLog.owner_user_id.in_(accessible_ids))
        .order_by(AuditLog.created_at.desc())
    )
    if action_filter:
        stmt = stmt.where(AuditLog.action == action_filter)
    if subject_type_filter:
        stmt = stmt.where(AuditLog.subject_type == subject_type_filter)
    if since:
        stmt = stmt.where(AuditLog.created_at >= since)
    if until:
        stmt = stmt.where(AuditLog.created_at < until)
    if actor_user_id is not None:
        stmt = stmt.where(AuditLog.actor_user_id == actor_user_id)
    if q:
        pat = f"%{q}%"
        stmt = stmt.where(
            (User.first_name.ilike(pat))
            | (AuditLog.action.ilike(pat))
            | (AuditLog.subject_type.ilike(pat))
        )
    return stmt


@router.get("/audit", response_model=list[AuditEntryView])
async def list_audit(
    owner_id: int | None = Query(default=None),
    action_filter: str | None = Query(default=None, alias="action"),
    subject_type_filter: str | None = Query(default=None, alias="subject_type"),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    q: str | None = Query(default=None, max_length=64),
    actor_user_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    stmt = _audit_stmt_base(
        ctx, owner_id, action_filter, subject_type_filter, since, until, q, actor_user_id
    ).offset(offset).limit(limit)
    rows = (await db.execute(stmt)).all()
    return [
        AuditEntryView(
            id=a.id,
            owner_user_id=a.owner_user_id,
            actor_user_id=a.actor_user_id,
            actor_display_name=u.first_name,
            actor_role=a.actor_role,
            action=a.action,
            subject_type=a.subject_type,
            subject_id=a.subject_id,
            payload=a.payload,
            created_at=a.created_at,
        )
        for (a, u) in rows
    ]


@router.get("/audit.csv")
async def audit_csv(
    owner_id: int | None = Query(default=None),
    action_filter: str | None = Query(default=None, alias="action"),
    subject_type_filter: str | None = Query(default=None, alias="subject_type"),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    q: str | None = Query(default=None, max_length=64),
    actor_user_id: int | None = Query(default=None),
    ctx: AuthContext = Depends(require_verified_partner),
    db: AsyncSession = Depends(get_db),
):
    import csv
    import io
    import json as _json
    from fastapi.responses import StreamingResponse

    stmt = _audit_stmt_base(
        ctx, owner_id, action_filter, subject_type_filter, since, until, q, actor_user_id
    )
    rows = (await db.execute(stmt)).all()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["created_at", "actor", "actor_role", "action", "subject_type", "subject_id", "payload"])
    for (a, u) in rows:
        w.writerow([
            a.created_at.isoformat(),
            u.first_name or "",
            a.actor_role,
            a.action,
            a.subject_type or "",
            a.subject_id if a.subject_id is not None else "",
            _json.dumps(a.payload, ensure_ascii=False) if a.payload is not None else "",
        ])
    today = datetime.now(timezone.utc).date().isoformat()
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="audit-{today}.csv"'},
    )

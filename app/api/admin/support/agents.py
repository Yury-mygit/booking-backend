"""Admin roster: список support-agents + добавление/изменение/удаление
(soft-delete через removed_at) + user-search для формы добавления.

Все требуют `require_superadmin` (не support-agent) — это управление
самим roster'ом, отдельная ось прав.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import String, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import AuthContext, require_superadmin
from app.core.exceptions import APIError
from app.models.models import User
from app.models.support import SupportAgent
from app.schemas.support import AgentAddIn, AgentOut, AgentPatchIn, UserSearchOut
from app.services.support import tickets as svc_tickets

from . import _common as conv

router = APIRouter(tags=["admin-support"])


# ─── roster CRUD ────────────────────────────────────────────────────


@router.get("/agents", response_model=list)
async def list_agents(
    include_removed: bool = False,
    ctx: AuthContext = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> list:
    q = select(SupportAgent)
    if not include_removed:
        q = q.where(SupportAgent.removed_at.is_(None))
    q = q.order_by(
        SupportAgent.removed_at.is_(None).desc(),
        SupportAgent.added_at.desc(),
    )
    rows = await db.execute(q)
    agents = list(rows.scalars().all())

    uids = set()
    for a in agents:
        uids.add(a.user_id)
        uids.add(a.added_by_user_id)
        if a.removed_by_user_id:
            uids.add(a.removed_by_user_id)
    users_map = await svc_tickets.get_users_map(db, ids=list(uids))

    return [
        conv.agent_out(
            a,
            user=users_map[a.user_id],
            added_by=users_map.get(a.added_by_user_id),
            removed_by=users_map.get(a.removed_by_user_id) if a.removed_by_user_id else None,
        ).model_dump(mode="json")
        for a in agents
    ]


@router.post("/agents", response_model=AgentOut, status_code=201)
async def add_agent(
    body: AgentAddIn,
    ctx: AuthContext = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> AgentOut:
    target = await db.get(User, body.user_id)
    if target is None:
        raise APIError(404, "user_not_found", f"User {body.user_id} not found")

    existing = await db.execute(
        select(SupportAgent).where(
            SupportAgent.user_id == body.user_id,
            SupportAgent.removed_at.is_(None),
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise APIError(409, "already_agent", "User is already an active support agent")

    agent = SupportAgent(
        user_id=body.user_id,
        is_lead=body.is_lead,
        note=body.note,
        added_by_user_id=ctx.user.id,
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)

    return conv.agent_out(agent, user=target, added_by=ctx.user, removed_by=None)


@router.patch("/agents/{agent_id}", response_model=AgentOut)
async def patch_agent(
    agent_id: int,
    body: AgentPatchIn,
    ctx: AuthContext = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> AgentOut:
    agent = await db.get(SupportAgent, agent_id)
    if agent is None or agent.removed_at is not None:
        raise APIError(404, "agent_not_found", "Active agent not found")

    if body.is_lead is not None:
        agent.is_lead = body.is_lead
    # note=None означает «оставить как есть» (PatchIn null = пропускаем),
    # пустая строка — явное стирание.
    if body.note is not None:
        agent.note = body.note or None

    await db.commit()
    await db.refresh(agent)

    user = await db.get(User, agent.user_id)
    added_by = await db.get(User, agent.added_by_user_id)
    return conv.agent_out(agent, user=user, added_by=added_by, removed_by=None)


@router.delete("/agents/{agent_id}", status_code=204)
async def remove_agent(
    agent_id: int,
    ctx: AuthContext = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Soft-delete. Историческое назначение/audit/тикеты не теряются."""
    agent = await db.get(SupportAgent, agent_id)
    if agent is None:
        raise APIError(404, "agent_not_found", "Agent not found")
    if agent.removed_at is not None:
        return  # idempotent
    agent.removed_at = datetime.now(timezone.utc)
    agent.removed_by_user_id = ctx.user.id
    await db.commit()


# ─── users search (для формы добавления агента) ────────────────────


@router.get("/users/search", response_model=list)
async def users_search(
    q: str,
    limit: int = 20,
    ctx: AuthContext = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> list:
    if not q or len(q.strip()) < 2:
        raise APIError(400, "bad_query", "query must be at least 2 chars")
    q_pat = f"%{q.strip()}%"
    rows = await db.execute(
        select(User).where(
            or_(
                User.first_name.ilike(q_pat),
                User.last_name.ilike(q_pat),
                User.username.ilike(q_pat),
                cast(User.telegram_id, String).ilike(q_pat),
            )
        ).limit(min(max(limit, 1), 50))
    )
    users = list(rows.scalars().all())
    return [
        UserSearchOut(
            id=u.id, telegram_id=u.telegram_id,
            first_name=u.first_name, last_name=u.last_name, username=u.username,
            role=u.role, is_superadmin=u.is_superadmin,
        ).model_dump(mode="json")
        for u in users
    ]

"""Admin support chat API: `/admin/support/*` (карта #92).

Доступ: `User.role == admin OR User.is_superadmin` — без отдельной
roster-таблицы. Endpoints зеркалят user-side, но скоупом — все
thread'ы во всех block'ах.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import AuthContext, current_user
from app.core.exceptions import APIError
from app.models.models import UserRole
from app.models.support import SupportSenderKind
from app.schemas.support import (
    MessageCreateAdmin,
    MessageOut,
    ReadMarkAdmin,
    ThreadOutAdmin,
    UserMini,
)
from app.services.support import chat as svc_chat
from app.services.support import realtime

router = APIRouter(prefix="/support", tags=["admin-support"])

_HEARTBEAT_SECONDS = 30


# ─── permission ───────────────────────────────────────────────────────


async def require_support_admin(
    ctx: AuthContext = Depends(current_user),
) -> AuthContext:
    """role=admin OR is_superadmin."""
    if ctx.user.role != UserRole.admin and not ctx.user.is_superadmin:
        raise APIError(403, "forbidden", "Support admin access required")
    return ctx


# ─── threads list / detail ────────────────────────────────────────────


@router.get("/threads", response_model=list[ThreadOutAdmin])
async def list_threads(
    before_last_msg_at: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    _ctx: AuthContext = Depends(require_support_admin),
    db: AsyncSession = Depends(get_db),
) -> list[ThreadOutAdmin]:
    rows = await svc_chat.list_threads_admin(db, before_last_msg_at, limit)
    out: list[ThreadOutAdmin] = []
    for thread, user, preview, unread in rows:
        if user is None:
            continue
        out.append(ThreadOutAdmin(
            id=thread.id,
            block=thread.block,
            user=UserMini.model_validate(user),
            last_message_at=thread.last_message_at,
            last_message_preview=preview,
            unread_count=unread,
        ))
    return out


@router.get("/threads/{thread_id}", response_model=ThreadOutAdmin)
async def get_thread(
    thread_id: int,
    _ctx: AuthContext = Depends(require_support_admin),
    db: AsyncSession = Depends(get_db),
) -> ThreadOutAdmin:
    thread = await svc_chat.get_thread_by_id(db, thread_id)
    if thread is None:
        raise APIError(404, "not_found", "Thread not found")
    from app.models.models import User
    user = await db.get(User, thread.user_id)
    if user is None:
        raise APIError(404, "not_found", "Thread owner missing")
    unread = await svc_chat.count_unread_for_admin(db, thread)
    # Last message preview — отдельный запрос (одна строка).
    msgs = await svc_chat.list_messages(db, thread.id, before_id=None, limit=1)
    preview = svc_chat.truncate(msgs[0].body, svc_chat.MAX_PREVIEW) if msgs else None
    return ThreadOutAdmin(
        id=thread.id,
        block=thread.block,
        user=UserMini.model_validate(user),
        last_message_at=thread.last_message_at,
        last_message_preview=preview,
        unread_count=unread,
    )


@router.get("/threads/{thread_id}/messages", response_model=list[MessageOut])
async def list_thread_messages(
    thread_id: int,
    before_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _ctx: AuthContext = Depends(require_support_admin),
    db: AsyncSession = Depends(get_db),
) -> list[MessageOut]:
    thread = await svc_chat.get_thread_by_id(db, thread_id)
    if thread is None:
        raise APIError(404, "not_found", "Thread not found")
    msgs = await svc_chat.list_messages(db, thread.id, before_id, limit)
    return [MessageOut.model_validate(m) for m in msgs]


@router.post("/threads/{thread_id}/messages", response_model=MessageOut)
async def post_admin_message(
    thread_id: int,
    payload: MessageCreateAdmin,
    ctx: AuthContext = Depends(require_support_admin),
    db: AsyncSession = Depends(get_db),
) -> MessageOut:
    thread = await svc_chat.get_thread_by_id(db, thread_id)
    if thread is None:
        raise APIError(404, "not_found", "Thread not found")
    msg, _ = await svc_chat.send_message(
        db, thread, ctx.user.id, SupportSenderKind.admin, payload.body
    )

    # Локалы для post-commit emit/notify.
    t_id = thread.id
    block_value = thread.block.value
    msg_id = msg.id
    sender_kind = msg.sender_kind.value
    body = msg.body
    created_at_iso = msg.created_at.isoformat()
    owner_user_id = thread.user_id

    realtime.emit_message(
        t_id, owner_user_id, block_value, msg_id,
        sender_kind, body, created_at_iso,
    )

    return MessageOut.model_validate(msg)


@router.post("/threads/{thread_id}/read", status_code=204)
async def mark_thread_read(
    thread_id: int,
    payload: ReadMarkAdmin,
    _ctx: AuthContext = Depends(require_support_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    thread = await svc_chat.get_thread_by_id(db, thread_id)
    if thread is None:
        return Response(status_code=204)
    await svc_chat.mark_read(
        db, thread, SupportSenderKind.admin, payload.up_to_message_id
    )
    return Response(status_code=204)


@router.get("/events/sse")
async def sse_admin(
    request: Request,
    _ctx: AuthContext = Depends(require_support_admin),
) -> StreamingResponse:
    async def gen():
        yield "retry: 5000\n\n"
        sub = realtime.subscribe_admin()
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(
                        sub.__anext__(), timeout=_HEARTBEAT_SECONDS
                    )
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            await sub.aclose()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

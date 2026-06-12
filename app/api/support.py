"""User-side support chat API: `/api/v1/support/*` (карта #92).

Любой залогиненный user пишет в свой thread по `block`
(client/partner). Один user может иметь два независимых thread'а.
Thread создаётся лениво на первое сообщение.

Admin endpoints — отдельно в `api/admin/support.py`.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import AuthContext, current_user
from app.models.support import SupportBlock, SupportSenderKind
from app.schemas.support import (
    MessageCreateUser,
    MessageOut,
    ReadMarkUser,
    ThreadOutUser,
)
from app.services.support import chat as svc_chat
from app.services.support import notifications as svc_notify
from app.services.support import realtime

router = APIRouter(prefix="/support", tags=["support"])

_HEARTBEAT_SECONDS = 30


@router.get("/thread", response_model=ThreadOutUser | None)
async def get_my_thread(
    block: SupportBlock = Query(...),
    ctx: AuthContext = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> ThreadOutUser | None:
    thread = await svc_chat.get_thread(db, ctx.user.id, block)
    if thread is None:
        return None
    return ThreadOutUser(
        id=thread.id,
        block=thread.block,
        last_message_at=thread.last_message_at,
        has_unread=svc_chat.has_unread_for_user(thread),
    )


@router.get("/thread/messages", response_model=list[MessageOut])
async def get_my_messages(
    block: SupportBlock = Query(...),
    before_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    ctx: AuthContext = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[MessageOut]:
    thread = await svc_chat.get_thread(db, ctx.user.id, block)
    if thread is None:
        return []
    msgs = await svc_chat.list_messages(db, thread.id, before_id, limit)
    return [MessageOut.model_validate(m) for m in msgs]


@router.post("/thread/messages", response_model=MessageOut)
async def post_my_message(
    payload: MessageCreateUser,
    ctx: AuthContext = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageOut:
    thread, created = await svc_chat.get_or_create_thread(
        db, ctx.user.id, payload.block
    )
    msg, _ = await svc_chat.send_message(
        db, thread, ctx.user.id, SupportSenderKind.user, payload.body
    )

    # Локалы для post-commit emit/notify.
    thread_id = thread.id
    block_value = thread.block.value
    msg_id = msg.id
    sender_kind = msg.sender_kind.value
    body = msg.body
    created_at_iso = msg.created_at.isoformat()
    user_id = ctx.user.id

    if created:
        realtime.emit_thread_created(thread_id, user_id, block_value)
    realtime.emit_message(
        thread_id, user_id, block_value, msg_id,
        sender_kind, body, created_at_iso,
    )
    asyncio.create_task(
        svc_notify.notify_admins_on_user_message(thread_id, msg_id)
    )

    return MessageOut.model_validate(msg)


@router.post("/thread/read", status_code=204)
async def mark_my_read(
    payload: ReadMarkUser,
    ctx: AuthContext = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    thread = await svc_chat.get_thread(db, ctx.user.id, payload.block)
    if thread is None:
        return Response(status_code=204)
    await svc_chat.mark_read(
        db, thread, SupportSenderKind.user, payload.up_to_message_id
    )
    return Response(status_code=204)


@router.get("/events/sse")
async def sse_user(
    request: Request,
    block: SupportBlock = Query(...),
    ctx: AuthContext = Depends(current_user),
) -> StreamingResponse:
    user_id = ctx.user.id
    block_value = block.value

    async def gen():
        yield "retry: 5000\n\n"
        sub = realtime.subscribe_user(user_id, block_value)
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

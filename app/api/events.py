"""SSE endpoint: clients subscribe to refresh-pings per hotel."""
import asyncio
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import pubsub
from app.core.database import get_db
from app.core.exceptions import APIError
from app.models.models import Hotel, HotelStatus

router = APIRouter(prefix="/public", tags=["events"])

# How long to wait for an event before sending a keepalive comment.
# Must stay well below any proxy idle-timeout (Caddy default is generous,
# but 30s is the conventional SSE heartbeat).
_HEARTBEAT_SECONDS = 30


async def _resolve_hotel_id(db: AsyncSession, slug_or_id: str) -> int:
    stmt = select(Hotel.id).where(Hotel.status == HotelStatus.published)
    if slug_or_id.isdigit():
        stmt = stmt.where(Hotel.id == int(slug_or_id))
    else:
        stmt = stmt.where(Hotel.slug == slug_or_id)
    hid = (await db.execute(stmt)).scalar_one_or_none()
    if hid is None:
        raise APIError(404, "not_found", "Hotel not found")
    return hid


@router.get("/hotels/{slug_or_id}/events")
async def hotel_events(
    slug_or_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    hotel_id = await _resolve_hotel_id(db, slug_or_id)

    async def gen():
        # Initial comment so the client sees headers immediately and EventSource
        # transitions to OPEN. Also tells the browser to retry in 5s on drop.
        yield "retry: 5000\n\n"
        sub = pubsub.subscribe(hotel_id)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(sub.__anext__(), timeout=_HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            await sub.aclose()

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)

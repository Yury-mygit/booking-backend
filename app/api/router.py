from fastapi import APIRouter

from app.api import (
    admin,
    auth,
    chat_client,
    client,
    events,
    media_refs,
    partner,
    payments,
    public,
    qr,
    support,
    tg,
    uploads,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(public.router)
api_router.include_router(events.router)
api_router.include_router(client.router)
api_router.include_router(chat_client.router)
api_router.include_router(payments.router)
api_router.include_router(partner.router)
api_router.include_router(admin.router)
api_router.include_router(support.router)
api_router.include_router(tg.router)
api_router.include_router(uploads.router)
api_router.include_router(qr.router)
api_router.include_router(media_refs.router)

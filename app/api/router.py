from fastapi import APIRouter

from app.api import admin, auth, client, partner, public

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(public.router)
api_router.include_router(client.router)
api_router.include_router(partner.router)
api_router.include_router(admin.router)

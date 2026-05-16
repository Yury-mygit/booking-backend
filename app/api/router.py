from fastapi import APIRouter

from app.api import auth, client, public

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(public.router)
api_router.include_router(client.router)

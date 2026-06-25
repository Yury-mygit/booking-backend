import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.info import router as info_router
from app.api.router import api_router
from app.core.autocancel import autocancel_loop
from app.core.config import settings
from app.core.exceptions import APIError

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    background = [
        asyncio.create_task(autocancel_loop()),
    ]
    try:
        yield
    finally:
        for t in background:
            t.cancel()
        for t in background:
            try:
                await t
            except asyncio.CancelledError:
                pass


app = FastAPI(title=settings.service_name, version=settings.version, lifespan=lifespan)
app.include_router(info_router)
app.include_router(api_router, prefix="/api/v1")


@app.exception_handler(APIError)
async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    body: dict = {"error": exc.error, "message": exc.message}
    if exc.detail is not None:
        body["detail"] = exc.detail
    return JSONResponse(status_code=exc.status_code, content=body)

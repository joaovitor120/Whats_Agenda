from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.admin import router as admin_router
from app.api.evolution_webhook import router as evolution_router
from app.api.health import router as health_router
from app.api.oauth import router as oauth_router
from app.core.config import settings
from app.core.logging import configure_logging

configure_logging()
production = settings.app_env == "production"
app = FastAPI(
    title="Whats Agenda",
    version="0.2.0",
    docs_url=None if production else "/docs",
    redoc_url=None,
    openapi_url=None if production else "/openapi.json",
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    if production and request.url.scheme != "https" and not request.url.path.startswith("/health/"):
        response = JSONResponse({"detail": "HTTPS required"}, status_code=400)
    else:
        try:
            response = await call_next(request)
        except Exception:
            response = JSONResponse({"detail": "Service temporarily unavailable"}, status_code=503)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    if production:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    return response


app.include_router(health_router)
app.include_router(evolution_router)
app.include_router(admin_router)
app.include_router(oauth_router)


@app.get("/")
async def root():
    return {"name": "Whats Agenda", "status": "running"}

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .database import init_db
from .scheduler import start_scheduler, stop_scheduler
from .api import (
    routes_accounts, routes_posts, routes_analytics, routes_automations,
    routes_billing, routes_media, routes_schedules, routes_inbox,
    routes_visuals, routes_sources,
)
from .mcp import server as mcp_server

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    description=(
        "Self-hosted Blotato-compatible social media API and MCP server: publish, "
        "schedule, comments, DMs, automations, analytics, visuals and source repurposing."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# REST surface
app.include_router(routes_accounts.router)
app.include_router(routes_posts.router)
app.include_router(routes_schedules.router)
app.include_router(routes_analytics.router)
app.include_router(routes_inbox.comments_router)
app.include_router(routes_inbox.messages_router)
app.include_router(routes_automations.router)
app.include_router(routes_billing.router)
app.include_router(routes_media.router)
app.include_router(routes_visuals.router)
app.include_router(routes_sources.router)

# Webhook ingress
app.include_router(routes_automations.webhook_router)

# MCP
app.include_router(mcp_server.router)

# Static assets and uploaded media
Path(settings.MEDIA_DIR).mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=str(settings.MEDIA_DIR)), name="media")
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/health")
async def health_check():
    return {
        "status": "online",
        "service": "Blotato Self-Hosted Engine",
        "version": settings.VERSION,
        "mcpEndpoint": "/mcp",
        "restEndpoint": "/v2",
        "ui": "/" if (STATIC_DIR / "index.html").exists() else None,
    }


@app.get("/")
async def root():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse({
        "service": "Blotato Self-Hosted Engine",
        "version": settings.VERSION,
        "docs": "/docs",
        "ui": "not built - run the frontend build",
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("blotato_engine.main:app", host="0.0.0.0", port=settings.PORT, reload=settings.DEBUG)

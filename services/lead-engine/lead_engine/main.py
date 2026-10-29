from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .database import init_db
from .routes import auth_routes, workspace, contacts, search, folders, enrich, verify, mcp

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

try:  # campaigns module is optional at build time
    from .routes import campaigns  # type: ignore
except ImportError:  # pragma: no cover
    campaigns = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="Lead Engine",
    version=settings.VERSION,
    description=(
        "Self-hosted B2B contact enrichment: LinkedIn URL to verified work email, "
        "direct email and phone, plus prospecting, lists, CSV enrichment, verifier and campaigns."
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

app.include_router(auth_routes.router)
app.include_router(workspace.router)
app.include_router(contacts.router)
app.include_router(search.router)
app.include_router(folders.router)
app.include_router(enrich.router)
app.include_router(verify.router)
app.include_router(mcp.router)

if campaigns is not None:
    app.include_router(campaigns.router)
    app.include_router(campaigns.public_router)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/health")
async def health():
    return {
        "status": "online",
        "service": "Lead Engine",
        "version": settings.VERSION,
        "api": "/api",
        "mcp": "/mcp",
        "ui": "/" if (STATIC_DIR / "index.html").exists() else None,
        "campaignsLoaded": campaigns is not None,
    }


@app.get("/")
async def root():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse({
        "service": "Lead Engine",
        "version": settings.VERSION,
        "docs": "/docs",
        "ui": "not built",
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("lead_engine.main:app", host=settings.HOST, port=settings.PORT, reload=False)

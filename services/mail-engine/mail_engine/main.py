from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .database import init_db
from .routes import auth_routes, workspace, campaigns, leads, sending, inbox, warmup, analytics, mcp

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="Mail Engine",
    version=settings.VERSION,
    description=(
        "Self-hosted cold email engine: campaigns, multi-step sequences, mailboxes, "
        "warmup simulation, inbox, suppressions and analytics. Sending is simulated by default."
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
app.include_router(campaigns.router)
app.include_router(leads.router)
app.include_router(sending.router)
app.include_router(inbox.router)
app.include_router(warmup.router)
app.include_router(analytics.router)
app.include_router(mcp.router)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/health")
async def health():
    return {
        "status": "online",
        "service": "Mail Engine",
        "version": settings.VERSION,
        "api": "/api",
        "mcp": "/mcp",
        "ui": "/" if (STATIC_DIR / "index.html").exists() else None,
        "sendMode": settings.SEND_MODE,
    }


@app.get("/")
async def root():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse({
        "service": "Mail Engine",
        "version": settings.VERSION,
        "docs": "/docs",
        "ui": "not built",
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("mail_engine.main:app", host=settings.HOST, port=settings.PORT, reload=False)

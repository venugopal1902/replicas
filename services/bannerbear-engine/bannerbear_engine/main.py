from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from .config import settings
from .database import init_db
from .routes import router

BASE_DIR = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title=settings.APP_NAME, version=settings.VERSION,
              description="Self-hosted, project-scoped template image rendering API.", lifespan=lifespan)
app.include_router(router)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    return response


@app.get("/health")
async def health():
    return {"status": "online", "service": settings.APP_NAME, "version": settings.VERSION,
            "port": settings.PORT, "admin": "/admin", "api_docs": "/docs"}


@app.get("/admin", include_in_schema=False)
async def admin_console():
    path = BASE_DIR / "static" / "admin.html"
    if not path.is_file():
        return JSONResponse({"detail": "Admin console is not installed"}, status_code=404)
    return FileResponse(path, media_type="text/html; charset=utf-8")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("bannerbear_engine.main:app", host=settings.HOST, port=settings.PORT, reload=False)

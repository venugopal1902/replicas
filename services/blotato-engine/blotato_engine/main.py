from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .config import settings
from .database import init_db
from .scheduler import start_scheduler, stop_scheduler
from .api import routes_accounts, routes_posts, routes_analytics, routes_automations, routes_billing
from .mcp import server as mcp_server

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    start_scheduler()
    yield
    # Shutdown
    stop_scheduler()

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    description="Drop-in self-hosted Blotato unified social media API and MCP server with multi-tenant API key billing.",
    lifespan=lifespan
)

# Enable CORS for frontend clients (n8n, web dashboard, Cursor, Claude)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Blotato v2 API Routers
app.include_router(routes_accounts.router)
app.include_router(routes_posts.router)
app.include_router(routes_analytics.router)
app.include_router(routes_automations.router)
app.include_router(routes_billing.router)

# Mount MCP Router
app.include_router(mcp_server.router)

@app.get("/")
@app.get("/health")
async def health_check():
    return {
        "status": "online",
        "service": "Blotato Self-Hosted Engine",
        "version": settings.VERSION,
        "mcpEndpoint": "/mcp",
        "restEndpoint": "/v2"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("blotato_engine.main:app", host="0.0.0.0", port=settings.PORT, reload=settings.DEBUG)

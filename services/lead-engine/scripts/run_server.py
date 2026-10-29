import sys
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lead_engine.config import settings

if __name__ == "__main__":
    print("\n" + "=" * 62)
    print("LEAD ENGINE")
    print("=" * 62)
    print(f"Version:      {settings.VERSION}")
    print(f"UI:           http://localhost:{settings.PORT}/")
    print(f"API:          http://localhost:{settings.PORT}/api")
    print(f"MCP:          http://localhost:{settings.PORT}/mcp")
    print(f"Swagger:      http://localhost:{settings.PORT}/docs")
    print(f"Login:        {settings.ADMIN_EMAIL} / {settings.ADMIN_PASSWORD}")
    print("=" * 62 + "\n")
    uvicorn.run("lead_engine.main:app", host=settings.HOST, port=settings.PORT, reload=False)

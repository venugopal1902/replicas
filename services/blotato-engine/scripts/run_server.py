import sys
from pathlib import Path
import uvicorn

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blotato_engine.config import settings

if __name__ == "__main__":
    print("\n" + "="*60)
    print("🚀 STARTING BLOTATO SELF-HOSTED ENGINE")
    print("="*60)
    print(f"Version:       {settings.VERSION}")
    print(f"Port:          {settings.PORT}")
    print(f"Master Key:    {settings.MASTER_API_KEY}")
    print(f"REST Base URL: http://localhost:{settings.PORT}/v2")
    print(f"MCP Endpoint:  http://localhost:{settings.PORT}/mcp")
    print(f"Swagger Docs:  http://localhost:{settings.PORT}/docs")
    print("="*60 + "\n")
    
    uvicorn.run("blotato_engine.main:app", host="0.0.0.0", port=settings.PORT, reload=False)

"""Run Zigpoll Engine without depending on the current working directory."""
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from zigpoll_engine.config import settings

if __name__ == "__main__":
    print("Zigpoll Engine")
    print(f"Admin: http://127.0.0.1:{settings.PORT}/")
    print(f"API docs: http://127.0.0.1:{settings.PORT}/docs")
    print(f"Health: http://127.0.0.1:{settings.PORT}/health")
    uvicorn.run("zigpoll_engine.main:app", host=settings.HOST, port=settings.PORT, reload=False)

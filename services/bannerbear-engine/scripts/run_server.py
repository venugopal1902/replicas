import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bannerbear_engine.config import settings

if __name__ == "__main__":
    print("Bannerbear Engine")
    print(f"Admin: http://127.0.0.1:{settings.PORT}/admin")
    print(f"API docs: http://127.0.0.1:{settings.PORT}/docs")
    print(f"Health: http://127.0.0.1:{settings.PORT}/health")
    uvicorn.run("bannerbear_engine.main:app", host=settings.HOST, port=settings.PORT, reload=False)

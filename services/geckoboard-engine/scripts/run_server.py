import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn

from geckoboard_engine.config import settings

if __name__ == "__main__":
    uvicorn.run("geckoboard_engine.main:app", host=settings.host, port=settings.port, reload=False)

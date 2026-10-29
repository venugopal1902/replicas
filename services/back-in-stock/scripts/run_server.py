"""Run the Back in Stock server on port 8600."""
import os
import sys
from pathlib import Path

import uvicorn

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))


def main() -> None:
    port = int(os.environ.get("PORT", "8600"))
    uvicorn.run("back_in_stock.app:app", host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()

"""Run Churn Buster on port 8610."""
import os
import sys
from pathlib import Path

import uvicorn

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))


def main() -> None:
    uvicorn.run(
        "churn_buster.app:app",
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "8610")),
        log_level="warning",
    )


if __name__ == "__main__":
    main()

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geckoboard_engine.database import init_db, seed_demo


def main() -> None:
    init_db()
    seed_demo()
    print("Demo seed ready: 2 datasets, 2 dashboards, 1 TV rotation.")


if __name__ == "__main__":
    main()

"""Reset Lead Engine to a clean, believable demo state.

Tests and manual probing write into the same SQLite file the app serves, which leaves duplicate
contacts and stray folders behind. This wipes the database and reseeds it.

    # stop the server first (Windows holds the file open), then:
    uv run python scripts/reset_demo_data.py --force

The seeded login is admin@leadengine.local with settings.ADMIN_PASSWORD (default leadengine123).
Change it afterwards with scripts/set_password.py.
"""
import argparse
import asyncio
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

DB_FILES = [
    BASE_DIR / "data" / "lead_engine.db",
    BASE_DIR / "data" / "lead_engine.db-wal",
    BASE_DIR / "data" / "lead_engine.db-shm",
]


async def reset() -> int:
    removed = 0
    for f in DB_FILES:
        if f.exists():
            try:
                f.unlink()
                removed += 1
            except OSError as exc:
                print(f"Could not delete {f.name}: {exc}")
                print("Stop the server first: the database file is locked.")
                return 1

    from lead_engine.database import init_db
    from lead_engine.models import Contact, Folder
    from lead_engine.database import AsyncSessionLocal
    from sqlalchemy import select, func

    await init_db()

    async with AsyncSessionLocal() as db:
        contacts = (await db.execute(select(func.count()).select_from(Contact))).scalar()
        folders = (await db.execute(select(func.count()).select_from(Folder))).scalar()

    print(f"Removed {removed} database file(s) and reseeded.")
    print(f"Contacts: {contacts} | Folders: {folders}")
    print("Login: admin@leadengine.local / leadengine123")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Reset Lead Engine demo data")
    ap.add_argument("--force", action="store_true", help="required; deletes all data")
    args = ap.parse_args()
    if not args.force:
        print("This deletes all contacts, folders, jobs and campaigns.")
        print("Re-run with --force to confirm.")
        return 1
    return asyncio.run(reset())


if __name__ == "__main__":
    raise SystemExit(main())

"""Drop every table and reseed the demo workspace. Destructive by design."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mail_engine.database import reset_demo_data
from mail_engine.config import settings


async def main() -> None:
    await reset_demo_data()
    print(f"Reseeded {settings.DATABASE_URL}")
    print(f"Login: {settings.ADMIN_EMAIL} / {settings.ADMIN_PASSWORD}")


if __name__ == "__main__":
    asyncio.run(main())

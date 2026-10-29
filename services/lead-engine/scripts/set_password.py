"""Set or reset a Lead Engine password, or create a user.

Examples
--------
  uv run python scripts/set_password.py --list
  uv run python scripts/set_password.py --email admin@leadengine.local --password mynewpass
  uv run python scripts/set_password.py --email me@corp.com --password secret123 --create --name "Me"
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from lead_engine.auth import hash_password
from lead_engine.config import settings
from lead_engine.database import AsyncSessionLocal, init_db
from lead_engine.models import User, Workspace, WorkspaceMember, Folder, ApiKey


async def main() -> int:
    parser = argparse.ArgumentParser(description="Manage Lead Engine accounts")
    parser.add_argument("--list", action="store_true", help="list accounts")
    parser.add_argument("--email", help="account email")
    parser.add_argument("--password", help="new password (min 8 characters)")
    parser.add_argument("--name", help="display name, used with --create")
    parser.add_argument("--create", action="store_true", help="create the account if it does not exist")
    parser.add_argument("--deactivate", action="store_true", help="disable this account instead")
    args = parser.parse_args()

    await init_db()

    async with AsyncSessionLocal() as db:
        if args.list:
            users = (await db.execute(select(User))).scalars().all()
            print(f"{'email':38s} {'active':6s} name")
            print("-" * 70)
            for u in users:
                print(f"{u.email:38s} {str(bool(u.is_active)):6s} {u.name or ''}")
            return 0

        if not args.email:
            parser.print_help()
            return 2

        email = args.email.lower().strip()
        user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()

        if args.deactivate:
            if not user:
                print(f"No account for {email}")
                return 1
            user.is_active = False
            await db.commit()
            print(f"Deactivated {email}")
            return 0

        if args.password and len(args.password) < 8:
            print("Password must be at least 8 characters.")
            return 2

        if user:
            if not args.password:
                print(f"{email} exists. Pass --password to set a new one.")
                return 2
            user.password_hash = hash_password(args.password)
            user.is_active = True
            await db.commit()
            print(f"Password updated for {email}")
            return 0

        if not args.create:
            print(f"No account for {email}. Re-run with --create to make one.")
            return 1
        if not args.password:
            print("--create requires --password")
            return 2

        user = User(
            email=email,
            password_hash=hash_password(args.password),
            name=args.name or email.split("@")[0],
            is_active=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

        ws = Workspace(
            name=f"{user.name}'s workspace",
            owner_id=user.id,
            plan="professional",
            credits=5000,
            verifier_credits=10000,
            seats=3,
            recipients_limit=10000,
            api_calls_limit=1500,
        )
        db.add(ws)
        await db.commit()
        await db.refresh(ws)

        db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
        db.add(Folder(workspace_id=ws.id, name="My default folder", created_by=user.id))
        db.add(ApiKey(
            workspace_id=ws.id,
            user_id=user.id,
            key=f"lke_live_{user.id.replace('-', '')[:32]}",
            label="default",
            calls_limit=1500,
        ))
        await db.commit()
        print(f"Created {email} with a professional workspace (5,000 credits).")
        print(f"Database: {settings.DATABASE_URL}")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

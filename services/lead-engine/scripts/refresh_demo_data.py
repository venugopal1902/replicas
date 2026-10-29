"""Refresh the demo dataset so every seeded domain actually has MX records.

Early demo data pointed at invented company domains, which resolve no MX and therefore can
never produce a work email. This rewrites companies and their contacts in place.

    uv run python scripts/refresh_demo_data.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from lead_engine.database import AsyncSessionLocal, COMPANIES, init_db
from lead_engine.models import Company, Contact


async def main() -> int:
    await init_db()

    async with AsyncSessionLocal() as db:
        companies = (await db.execute(select(Company).order_by(Company.created_at))).scalars().all()

        updated = 0
        for company, (new_name, domain, industry, headcount, location, founded) in zip(companies, COMPANIES):
            company.name = new_name
            company.domain = domain
            company.industry = industry
            company.headcount = headcount
            company.location = location
            company.founded_year = founded
            company.linkedin_url = f"https://www.linkedin.com/company/{new_name.lower()}"

            contacts = (await db.execute(
                select(Contact).where(Contact.company_id == company.id)
            )).scalars().all()
            for c in contacts:
                c.company_name = new_name
                c.domain = domain
                c.industry = industry
                c.headcount = headcount
                # a domain change invalidates any earlier candidate email
                if c.source == "database":
                    c.work_email = None
                    c.work_email_status = None
                    c.work_email_score = None
                    c.direct_email = None
                    c.direct_email_status = None
                    c.phone = None
                    c.revealed = False
            updated += 1

        await db.commit()
        print(f"Updated {updated} companies and their contacts.")
        print("Reveal a contact now: MX resolution will succeed for every seeded domain.")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

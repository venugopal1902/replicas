import json
import random
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

from .config import settings
from .models import (
    Base, User, Workspace, WorkspaceMember, Company, Contact, Folder, ApiKey,
)

engine = create_async_engine(settings.DATABASE_URL, echo=settings.DEBUG, future=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# Real domains so MX verification resolves during demos. Names are real companies,
# people are fictional. Emails are still pattern candidates verified by MX, never scraped.
COMPANIES = [
    ("Stripe", "stripe.com", "Fintech", "1000+", "San Francisco, US", 2010),
    ("Figma", "figma.com", "Design", "1000+", "San Francisco, US", 2012),
    ("Linear", "linear.app", "SaaS", "51-200", "San Francisco, US", 2019),
    ("Vercel", "vercel.com", "Cloud", "201-500", "San Francisco, US", 2015),
    ("Airtable", "airtable.com", "SaaS", "201-500", "San Francisco, US", 2012),
    ("GitHub", "github.com", "Cloud", "1000+", "San Francisco, US", 2008),
    ("Atlassian", "atlassian.com", "SaaS", "1000+", "Sydney, AU", 2002),
    ("HubSpot", "hubspot.com", "SaaS", "1000+", "Boston, US", 2006),
    ("Datadog", "datadoghq.com", "Cloud", "1000+", "New York, US", 2010),
    ("Shopify", "shopify.com", "E-commerce", "1000+", "Ottawa, CA", 2006),
    ("Intercom", "intercom.com", "SaaS", "201-500", "Dublin, IE", 2011),
    ("Zapier", "zapier.com", "SaaS", "201-500", "Remote", 2011),
]

TITLES = [
    ("Head of Talent", "head"),
    ("VP People", "vp"),
    ("Talent Acquisition Lead", "lead"),
    ("Founder & CEO", "founder"),
    ("Head of Sales", "head"),
    ("VP Revenue", "vp"),
    ("Director of Engineering", "director"),
    ("Product Manager", "manager"),
    ("Chief Technology Officer", "c-level"),
    ("Head of Marketing", "head"),
]

FIRST = ["Maya", "Arjun", "Chidi", "Sara", "Tom", "Priya", "Daniel", "Oliver", "Elena", "Marcus",
         "Nadia", "Ravi", "Sofia", "Liam", "Amara", "Noah", "Ines", "Kofi", "Lena", "Hugo",
         "Anika", "Theo", "Zara", "Felix", "Nora", "Idris", "Clara", "Milos", "Aisha", "Ben"]
LAST = ["Okafor", "Mehta", "Nwosu", "Qureshi", "Whitfield", "Raman", "Okoye", "Whitmore", "Petrova",
        "Lindqvist", "Haddad", "Iyer", "Moreno", "Byrne", "Diallo", "Fischer", "Costa", "Mensah",
        "Vogel", "Marchand", "Sharma", "Novak", "Hughes", "Braun", "Ferrari", "Adeyemi", "Dubois",
        "Petrov", "Rahman", "Clarke"]

LOCATIONS = ["London, UK", "Berlin, Germany", "Amsterdam, NL", "Dublin, Ireland",
             "Manchester, UK", "Edinburgh, UK", "San Francisco, US", "New York, US"]

INDUSTRIES = ["SaaS", "Fintech", "Consulting", "Design", "Cloud", "Healthcare", "E-commerce"]


async def init_db() -> None:
    from .auth import hash_password  # local import avoids a circular dependency

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Light additive migrations so existing SQLite databases pick up new columns.
    # Each ALTER is idempotent: a duplicate column raises and is ignored.
    for statement in (
        "ALTER TABLE workspaces ADD COLUMN hunter_key VARCHAR(255)",
        "ALTER TABLE workspaces ADD COLUMN apollo_key VARCHAR(255)",
        "ALTER TABLE workspaces ADD COLUMN pdl_key VARCHAR(255)",
        "ALTER TABLE workspaces ADD COLUMN provider_status VARCHAR(40) DEFAULT 'unconfigured'",
        "ALTER TABLE enrichment_jobs ADD COLUMN rows_json TEXT",
    ):
        try:
            async with engine.begin() as conn:
                await conn.exec_driver_sql(statement)
        except Exception:
            pass

    async with AsyncSessionLocal() as session:
        existing = (await session.execute(select(User).where(User.email == settings.ADMIN_EMAIL))).scalar_one_or_none()
        if existing:
            return

        user = User(
            email=settings.ADMIN_EMAIL,
            password_hash=hash_password(settings.ADMIN_PASSWORD),
            name=settings.ADMIN_NAME,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        ws = Workspace(
            name="My workspace",
            owner_id=user.id,
            plan="professional",
            credits=5000,
            verifier_credits=10000,
            seats=3,
            recipients_limit=10000,
            api_calls_limit=1500,
        )
        session.add(ws)
        await session.commit()
        await session.refresh(ws)

        session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
        session.add(ApiKey(
            workspace_id=ws.id, user_id=user.id,
            key="lke_live_" + "demo" * 8, label="default", calls_limit=1500,
        ))
        session.add(Folder(workspace_id=ws.id, name="My default folder", color="#6ee7b7", created_by=user.id))

        # Companies
        company_rows = []
        for name, domain, industry, headcount, location, founded in COMPANIES:
            c = Company(
                name=name, domain=domain, industry=industry, headcount=headcount,
                location=location, founded_year=founded,
                linkedin_url=f"https://www.linkedin.com/company/{name.lower()}",
                description=f"{name} is a {industry.lower()} company headquartered in {location}.",
            )
            session.add(c)
            company_rows.append(c)
        await session.commit()
        for c in company_rows:
            await session.refresh(c)

        # Contacts
        rng = random.Random(42)
        contacts = []
        for i in range(48):
            company = company_rows[i % len(company_rows)]
            title, seniority = TITLES[i % len(TITLES)]
            first = FIRST[(i * 7) % len(FIRST)]
            last = LAST[(i * 11) % len(LAST)]
            slug = f"{first.lower()}-{last.lower()}-{i}"
            contacts.append(Contact(
                workspace_id=ws.id,
                first_name=first,
                last_name=last,
                title=title,
                seniority=seniority,
                company_id=company.id,
                company_name=company.name,
                domain=company.domain,
                industry=company.industry,
                headcount=company.headcount,
                location=LOCATIONS[i % len(LOCATIONS)],
                linkedin_url=f"https://www.linkedin.com/in/{slug}",
                years_experience=rng.randint(3, 18),
                source="database",
                revealed=False,
            ))
        session.add_all(contacts)
        await session.commit()

        folder = (await session.execute(select(Folder).where(Folder.workspace_id == ws.id))).scalars().first()
        session.add_all([
            Contact(
                workspace_id=ws.id,
                first_name="Demo",
                last_name="Revealed",
                title="Head of Talent",
                seniority="head",
                company_id=company_rows[0].id,
                company_name=company_rows[0].name,
                domain=company_rows[0].domain,
                industry=company_rows[0].industry,
                headcount=company_rows[0].headcount,
                location="London, UK",
                linkedin_url="https://www.linkedin.com/in/demo-revealed",
                work_email="demo@northvale.io",
                work_email_status="verified",
                work_email_score="A+",
                direct_email="demo.revealed@gmail.com",
                direct_email_status="risky",
                direct_email_score="B",
                phone="+44 20 7946 0958",
                phone_source="provider",
                revealed=True,
                source="linkedin",
                enriched_at=datetime.utcnow(),
            )
        ])
        await session.commit()


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

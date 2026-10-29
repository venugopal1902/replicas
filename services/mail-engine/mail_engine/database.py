import random
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

from .config import settings
from .models import (
    Base, User, Workspace, Mailbox, Campaign, SequenceStep, Lead, Enrollment,
    Send, InboxMessage, Suppression, WarmupDay, Session,
)

engine = create_async_engine(settings.DATABASE_URL, echo=settings.DEBUG, future=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# Fictional people at fictional domains: demos never email a real inbox.
COMPANIES = [
    ("Northwind Data", "northwind-data.com", "Data Engineering"),
    ("Lumen Analytics", "lumenanalytics.io", "Analytics"),
    ("Kestrel Cloud", "kestrelcloud.dev", "Cloud Infrastructure"),
    ("Blue Harbor Health", "blueharbor.health", "Healthcare"),
    ("Vector Retail", "vectorretail.co", "Retail"),
    ("Alto Fintech", "altofintech.com", "Fintech"),
    ("Ridgeway Logistics", "ridgewaylogistics.com", "Logistics"),
    ("Sable Media", "sablemedia.tv", "Media"),
    ("Fernpath Software", "fernpath.io", "SaaS"),
    ("Cobalt Insurance", "cobaltinsure.com", "Insurance"),
]

FIRST = ["Maya", "Arjun", "Priya", "Daniel", "Elena", "Marcus", "Nadia", "Ravi", "Sofia",
         "Liam", "Amara", "Noah", "Ines", "Kofi", "Lena", "Hugo", "Anika", "Theo",
         "Zara", "Felix", "Nora", "Idris", "Clara", "Aisha", "Ben", "Meera", "Sanjay",
         "Tara", "Rahul", "Divya", "Oliver", "Grace", "Samir", "Neha", "Victor", "Ishita"]
LAST = ["Okafor", "Mehta", "Iyer", "Whitfield", "Petrova", "Lindqvist", "Haddad", "Raman",
        "Moreno", "Byrne", "Diallo", "Fischer", "Costa", "Mensah", "Vogel", "Marchand",
        "Sharma", "Novak", "Hughes", "Braun", "Ferrari", "Adeyemi", "Dubois", "Petrov",
        "Rahman", "Clarke", "Kulkarni", "Bose", "Nair", "Pillai"]
TITLES = ["Head of Data Engineering", "Director of Data Platform", "VP Engineering",
          "Senior Data Engineer", "Analytics Engineering Lead", "CTO", "Head of Analytics",
          "Data Architect", "Principal Data Engineer", "Head of BI"]

CAMPAIGN_SEEDS = [
    {
        "name": "Q4 Data Engineering Outreach",
        "status": "active",
        "subject": "Quick question about {{company}} data pipeline",
        "body": (
            "Hi {{first_name}},\n\n"
            "I have been following {{company}} and the way your team handles "
            "{{title}} problems stood out.\n\n"
            "We help data teams cut pipeline delivery time by half with a tested "
            "dbt plus Azure stack. Worth a 15 minute look next week?\n\n"
            "Best,\n{{sender_name}}"
        ),
        "steps": [
            {"step_order": 1, "delay_days": 0,
             "subject": "Quick question about {{company}} data pipeline",
             "body": "Hi {{first_name}},\n\nWe help data teams cut pipeline delivery time by half "
                     "with a tested dbt plus Azure stack.\n\nWorth a 15 minute look next week?\n\n"
                     "Best,\n{{sender_name}}"},
            {"step_order": 2, "delay_days": 3,
             "subject": "Re: Quick question about {{company}} data pipeline",
             "body": "Hi {{first_name}},\n\nFollowing up on my note. I put together a two page "
                     "summary of how {{company}} could reduce warehouse spend this quarter.\n\n"
                     "Want me to send it over?\n\n{{sender_name}}"},
            {"step_order": 3, "delay_days": 4,
             "subject": "Closing the loop, {{first_name}}",
             "body": "Hi {{first_name}},\n\nI will stop here so I do not clutter your inbox. "
                     "If pipeline reliability comes up at {{company}} this quarter, reply and I "
                     "will pick it back up.\n\n{{sender_name}}"},
        ],
    },
    {
        "name": "Azure Architect Roles",
        "status": "active",
        "subject": "{{company}} Azure migration, 20 min?",
        "body": (
            "Hi {{first_name}},\n\n"
            "Most Azure migrations stall on data governance, not compute. We ran three "
            "for teams your size last quarter.\n\n"
            "Open to a short call to compare notes?\n\n{{sender_name}}"
        ),
        "steps": [
            {"step_order": 1, "delay_days": 0,
             "subject": "{{company}} Azure migration, 20 min?",
             "body": "Hi {{first_name}},\n\nMost Azure migrations stall on data governance, not "
                     "compute. We ran three for teams your size last quarter.\n\n"
                     "Open to a short call?\n\n{{sender_name}}"},
            {"step_order": 2, "delay_days": 5,
             "subject": "Architecture review for {{company}}",
             "body": "Hi {{first_name}},\n\nOne more: I have a reference architecture checklist we "
                     "use for Azure data platforms. Happy to send it, no call needed.\n\n{{sender_name}}"},
        ],
    },
    {
        "name": "Replica Launch Announcement",
        "status": "draft",
        "subject": "Self-hosted replica of {{company}} tooling",
        "body": "Hi {{first_name}},\n\nWe open sourced self-hosted replicas of common sales tools. "
                "No seat pricing, runs on a laptop.\n\nWant the repo link?\n\n{{sender_name}}",
        "steps": [
            {"step_order": 1, "delay_days": 0,
             "subject": "Self-hosted replica of {{company}} tooling",
             "body": "Hi {{first_name}},\n\nWe open sourced self-hosted replicas of common sales "
                     "tools.\n\nWant the repo link?\n\n{{sender_name}}"},
        ],
    },
    {
        "name": "Warm Leads Follow-up",
        "status": "paused",
        "subject": "Picking this back up, {{first_name}}",
        "body": "Hi {{first_name}},\n\nYou mentioned timing was the issue at {{company}}. "
                "Is next month better?\n\n{{sender_name}}",
        "steps": [
            {"step_order": 1, "delay_days": 0,
             "subject": "Picking this back up, {{first_name}}",
             "body": "Hi {{first_name}},\n\nYou mentioned timing was the issue at {{company}}. "
                     "Is next month better?\n\n{{sender_name}}"},
            {"step_order": 2, "delay_days": 7, "subject": "One last note",
             "body": "Hi {{first_name}},\n\nLeaving this here in case it becomes useful.\n\n{{sender_name}}"},
        ],
    },
]

INBOX_SEEDS = [
    ("interested", "Re: Quick question about {{company}} data pipeline",
     "This is actually well timed. We are redoing our ingestion layer in Q1. Can you do Thursday 3pm IST?"),
    ("not_now", "Re: {{company}} Azure migration, 20 min?",
     "Not right now, we are mid migration. Ping me in February."),
    ("ooo", "Automatic reply: {{company}} Azure migration",
     "I am out of office until the 12th with limited access to email."),
    ("interested", "Re: Quick question about {{company}} data pipeline",
     "Send the two page summary. If it covers cost, I will forward it to my platform lead."),
    ("unsubscribe", "Re: Picking this back up, {{first_name}}",
     "Please remove me from this list."),
    ("interested", "Re: {{company}} Azure migration, 20 min?",
     "Who else in our space have you worked with? Need a reference before a call."),
    ("bounce", "Delivery Status Notification (Failure)",
     "Address not found. The recipient mailbox is unavailable."),
    ("not_now", "Re: Quick question about {{company}} data pipeline",
     "Budget is frozen until the new financial year. Try me in April."),
    ("interested", "Re: Azure migration architecture",
     "Yes, send the checklist. I will review it with our cloud team on Monday."),
]

WARMUP_DAYS = 14


async def init_db() -> None:
    from .auth import hash_password

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        existing = (await session.execute(
            select(User).where(User.email == settings.ADMIN_EMAIL)
        )).scalar_one_or_none()
        if existing:
            return

        rng = random.Random(11)
        now = datetime.utcnow()

        user = User(
            email=settings.ADMIN_EMAIL,
            password_hash=hash_password(settings.ADMIN_PASSWORD),
            name=settings.ADMIN_NAME,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        ws = Workspace(
            name="Northvale Growth",
            owner_id=user.id,
            plan="professional",
            daily_limit=200,
            sending_domain="northvale-outreach.com",
            warmup_enabled=True,
        )
        session.add(ws)
        await session.commit()
        await session.refresh(ws)

        mailboxes = []
        for i, (local, person, limit) in enumerate([
            ("venu", "Venu G", 50), ("maya", "Maya R", 40), ("arjun", "Arjun K", 30),
        ]):
            mb = Mailbox(
                workspace_id=ws.id,
                email=f"{local}@northvale-outreach.com",
                display_name=person,
                provider="simulated",
                status="active" if i < 2 else "paused",
                daily_limit=limit,
                sent_today=0,
                last_reset=date.today(),
                warmup_score=round(62 + i * 7 + rng.random() * 4, 1),
                warmup_enabled=True,
            )
            session.add(mb)
            mailboxes.append(mb)
        await session.commit()
        for mb in mailboxes:
            await session.refresh(mb)

        leads = []
        for i in range(42):
            company, domain, industry = COMPANIES[i % len(COMPANIES)]
            first = FIRST[(i * 7) % len(FIRST)]
            last = LAST[(i * 11) % len(LAST)]
            lead = Lead(
                workspace_id=ws.id,
                email=f"{first.lower()}.{last.lower()}@{domain}",
                first_name=first,
                last_name=last,
                company=company,
                title=TITLES[i % len(TITLES)],
                domain=domain,
                status="new",
                source="seed" if i % 3 else "csv-import",
            )
            session.add(lead)
            leads.append(lead)
        await session.commit()
        for lead in leads:
            await session.refresh(lead)

        campaigns = []
        for spec in CAMPAIGN_SEEDS:
            c = Campaign(
                workspace_id=ws.id,
                name=spec["name"],
                subject=spec["subject"],
                body=spec["body"],
                status=spec["status"],
                daily_limit=60,
                from_mailbox_id=mailboxes[0].id,
                activated_at=now - timedelta(days=12) if spec["status"] in ("active", "paused") else None,
            )
            session.add(c)
            await session.commit()
            await session.refresh(c)
            for step in spec["steps"]:
                session.add(SequenceStep(campaign_id=c.id, **step))
            campaigns.append(c)
        await session.commit()
        for c in campaigns:
            await session.refresh(c)

        sends = 0
        for campaign in campaigns[:2]:
            enrolled = leads[: 24 if campaign is campaigns[0] else 16]
            for idx, lead in enumerate(enrolled):
                days_ago = 12 - (idx % 12)
                sent_at = now - timedelta(days=days_ago, hours=rng.randint(1, 8))
                opened = rng.random() < 0.52
                clicked = opened and rng.random() < 0.28
                replied = opened and rng.random() < 0.16
                session.add(Send(
                    workspace_id=ws.id,
                    campaign_id=campaign.id,
                    lead_id=lead.id,
                    mailbox_id=mailboxes[idx % len(mailboxes)].id,
                    step_order=1,
                    to_email=lead.email,
                    subject=campaign.subject.replace("{{company}}", lead.company or "")
                                             .replace("{{first_name}}", lead.first_name or ""),
                    body=campaign.body,
                    status="sent",
                    tracking_id=f"seed{sends:04d}{rng.randrange(16 ** 6):06x}",
                    sent_at=sent_at,
                    opened_at=sent_at + timedelta(hours=2) if opened else None,
                    clicked_at=sent_at + timedelta(hours=3) if clicked else None,
                    replied_at=sent_at + timedelta(hours=5) if replied else None,
                ))
                sends += 1
                lead.status = "replied" if replied else ("opened" if opened else "contacted")
                session.add(Enrollment(
                    campaign_id=campaign.id,
                    lead_id=lead.id,
                    status="replied" if replied else "active",
                    current_step=1,
                    next_send_at=sent_at + timedelta(days=3),
                    last_sent_at=sent_at,
                ))
        await session.commit()

        for i, (classification, subject, body) in enumerate(INBOX_SEEDS):
            lead = leads[i % len(leads)]
            session.add(InboxMessage(
                workspace_id=ws.id,
                mailbox_id=mailboxes[i % len(mailboxes)].id,
                lead_id=lead.id,
                campaign_id=campaigns[0].id if i % 2 == 0 else campaigns[1].id,
                from_email=lead.email,
                from_name=f"{lead.first_name} {lead.last_name}",
                subject=subject.replace("{{first_name}}", lead.first_name or "")
                              .replace("{{company}}", lead.company or ""),
                body=body,
                classification=classification,
                is_read=i > 4,
                received_at=now - timedelta(days=i % 6, hours=rng.randint(1, 12)),
            ))

        for i, reason in enumerate(("unsubscribe", "bounce", "manual", "complaint")):
            lead = leads[30 + i]
            session.add(Suppression(
                workspace_id=ws.id,
                email=lead.email,
                reason=reason,
                created_at=now - timedelta(days=i + 1),
            ))

        for mb in mailboxes:
            for d in range(WARMUP_DAYS):
                day = date.today() - timedelta(days=WARMUP_DAYS - d - 1)
                sent = min(4 + d * 3, 40)
                spam = max(0, 3 - d // 5)
                session.add(WarmupDay(
                    mailbox_id=mb.id,
                    day=day,
                    sent=sent,
                    received=sent + rng.randint(0, 3),
                    landed_inbox=sent - spam,
                    spam=spam,
                    score=round(100 * (sent - spam) / max(sent, 1), 1),
                ))
            mb.warmup_score = round(min(99.0, 55 + WARMUP_DAYS * 2.6), 1)

        await session.commit()


async def reset_demo_data() -> None:
    """Drop and rebuild every table, then reseed. Used by scripts/reset_demo.py."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await init_db()


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

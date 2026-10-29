import json
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select
from .config import settings
from .models import (
    Base, ApiKeyModel, SocialAccountModel, CommentModel, MessageModel,
    ScheduleSlotModel, PostModel, AnalyticsSnapshotModel,
)

engine = create_async_engine(settings.DATABASE_URL, echo=settings.DEBUG, future=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

SEED_ACCOUNTS = [
    ("instagram", "Nandini Instagram", "nandini_official", {"provider": "composio", "account_slug": "instagram_scalt-lain"}),
    ("facebook", "Nandini Facebook Page", "nandini.page", {"provider": "mock", "note": "Plug Meta page token + page_id"}),
    ("twitter", "Venu X / Twitter", "venugopal1902", {"provider": "mock", "note": "Plug X API v2 bearer token"}),
    ("linkedin", "Venu LinkedIn", "venu-gopal", {"provider": "mock", "note": "Plug LinkedIn access token"}),
    ("youtube", "Nandini YouTube Shorts", "nandini_yt", {"provider": "mock", "note": "Plug Google OAuth token"}),
    ("threads", "Nandini Threads", "nandini_threads", {"provider": "mock", "note": "Plug Threads API token"}),
    ("bluesky", "Venu Bluesky", "venu.bsky.social", {"provider": "mock", "note": "Plug app password"}),
    ("pinterest", "Nandini Pinterest", "nandinipins", {"provider": "mock", "note": "Plug Pinterest token + board id"}),
    ("tiktok", "Nandini TikTok", "nandini.tok", {"provider": "mock", "note": "Plug TikTok content posting token"}),
]


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Lightweight migration for databases created before the column existed
        try:
            await conn.exec_driver_sql(
                "ALTER TABLE posts ADD COLUMN simulated BOOLEAN DEFAULT 0"
            )
        except Exception:
            pass

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ApiKeyModel).where(ApiKeyModel.api_key == settings.MASTER_API_KEY)
        )
        master = result.scalar_one_or_none()
        if master:
            return

        master = ApiKeyModel(
            api_key=settings.MASTER_API_KEY,
            client_name="Venu (Chief Bot Admin)",
            plan="admin",
            credits=-1,
            rate_limit_per_hour=999999,
            is_active=True,
        )
        session.add(master)
        await session.commit()
        await session.refresh(master)

        accounts = [
            SocialAccountModel(
                user_id=master.id,
                platform=platform,
                name=name,
                username=username,
                credentials_json=json.dumps(creds),
            )
            for platform, name, username, creds in SEED_ACCOUNTS
        ]
        session.add_all(accounts)
        await session.commit()

        for acc in accounts:
            await session.refresh(acc)

        by_platform = {a.platform: a for a in accounts}
        ig = by_platform["instagram"]
        x = by_platform["twitter"]
        li = by_platform["linkedin"]

        # Demo published posts so analytics and the dashboard are not empty
        demo_posts = [
            ("instagram", ig.id, "The hardest truth about relationships: love is never enough on its own. Respect is what holds two people together.",
             "https://www.instagram.com/reel/CxNandiniDemo1/", 18400, 1420, 96, 210, 640, 15900),
            ("instagram", ig.id, "Three habits that quietly kill momentum: rehearsing the plan, waiting for perfect, and telling everyone before you start.",
             "https://www.instagram.com/reel/CxNandiniDemo2/", 9200, 640, 41, 88, 310, 8100),
            ("twitter", x.id, "Ship the smallest version that proves the idea. Everything else is decoration.",
             "https://x.com/venugopal1902/status/1800000000000000001", 5400, 320, 28, 61, 44, 4900),
            ("linkedin", li.id, "Most data platforms fail on governance, not on Spark. Design the contract before the pipeline.",
             "https://www.linkedin.com/feed/update/urn:li:share:7200000000000000001", 7600, 410, 37, 52, 30, 6900),
        ]

        for platform, acc_id, text, url, views, likes, comments, shares, saves, reach in demo_posts:
            post = PostModel(
                user_id=master.id,
                account_id=acc_id,
                platform=platform,
                text=text,
                media_urls_json="[]",
                status="published",
                published_at=__import__("datetime").datetime.utcnow(),
                platform_post_id=url.rstrip("/").split("/")[-1],
                platform_post_url=url,
            )
            session.add(post)
            await session.commit()
            await session.refresh(post)
            session.add(AnalyticsSnapshotModel(
                user_id=master.id,
                post_submission_id=post.id,
                platform=platform,
                views=views,
                likes=likes,
                comments=comments,
                shares=shares,
                saves=saves,
                reach=reach,
            ))

        # Demo comments for the inbox
        demo_comments = [
            (ig.id, "instagram", "price", "priya.sharma", "price?"),
            (ig.id, "instagram", "link", "arjun_menon", "drop the link please"),
            (ig.id, "instagram", "love this", "kavya.r", "love this so much"),
            (x.id, "twitter", "great thread", "dev_rahul", "great thread, saving this"),
        ]
        for acc_id, platform, text, author, _ in demo_comments:
            session.add(CommentModel(
                user_id=master.id,
                account_id=acc_id,
                platform=platform,
                author=author,
                text=text,
            ))

        # Demo DM conversations
        demo_messages = [
            (ig.id, "instagram", "thread-priya", "priya.sharma", "hi! saw your reel about respect", "inbound"),
            (ig.id, "instagram", "thread-priya", "nandini_official", "hey! glad it landed. what are you working through?", "outbound"),
            (ig.id, "instagram", "thread-priya", "priya.sharma", "boundaries mostly. how do you set them without a fight?", "inbound"),
            (x.id, "twitter", "thread-rahul", "dev_rahul", "what stack did you use for the scheduler?", "inbound"),
        ]
        for acc_id, platform, thread, author, text, direction in demo_messages:
            session.add(MessageModel(
                user_id=master.id,
                account_id=acc_id,
                platform=platform,
                thread_id=thread,
                author=author,
                text=text,
                direction=direction,
                is_read=direction == "outbound",
            ))

        # Demo recurring slots
        for acc, day, hour in [(ig, 0, 9), (ig, 3, 18), (x, 1, 11)]:
            session.add(ScheduleSlotModel(
                user_id=master.id,
                account_id=acc.id,
                platform=acc.platform,
                day_of_week=day,
                hour=hour,
                minute=0,
            ))

        await session.commit()


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

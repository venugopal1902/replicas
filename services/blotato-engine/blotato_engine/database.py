import json
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select
from .config import settings
from .models import Base, ApiKeyModel, SocialAccountModel

engine = create_async_engine(settings.DATABASE_URL, echo=settings.DEBUG, future=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    # Seed master key if not present
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ApiKeyModel).where(ApiKeyModel.api_key == settings.MASTER_API_KEY)
        )
        master = result.scalar_one_or_none()
        if not master:
            master = ApiKeyModel(
                api_key=settings.MASTER_API_KEY,
                client_name="Venu (Chief Bot Admin)",
                plan="admin",
                credits=-1,  # unlimited
                rate_limit_per_hour=999999,
                is_active=True
            )
            session.add(master)
            await session.commit()
            await session.refresh(master)
            
            # Seed default starter accounts for Venu
            accounts = [
                SocialAccountModel(
                    user_id=master.id,
                    platform="instagram",
                    name="Nandini Instagram",
                    username="nandini_official",
                    credentials_json=json.dumps({"provider": "composio", "account_slug": "instagram_scalt-lain"})
                ),
                SocialAccountModel(
                    user_id=master.id,
                    platform="twitter",
                    name="Venu X / Twitter",
                    username="venugopal",
                    credentials_json=json.dumps({"provider": "mock", "note": "Plug X API v2 keys in settings"})
                ),
                SocialAccountModel(
                    user_id=master.id,
                    platform="linkedin",
                    name="Venu LinkedIn",
                    username="venu-gopal",
                    credentials_json=json.dumps({"provider": "mock", "note": "Plug LinkedIn Access Token"})
                ),
                SocialAccountModel(
                    user_id=master.id,
                    platform="youtube",
                    name="Nandini YouTube Shorts",
                    username="nandini_yt",
                    credentials_json=json.dumps({"provider": "mock", "note": "Plug Google OAuth / YouTube API"})
                ),
                SocialAccountModel(
                    user_id=master.id,
                    platform="threads",
                    name="Nandini Threads",
                    username="nandini_threads",
                    credentials_json=json.dumps({"provider": "mock"})
                ),
                SocialAccountModel(
                    user_id=master.id,
                    platform="bluesky",
                    name="Venu Bluesky",
                    username="venu.bsky.social",
                    credentials_json=json.dumps({"provider": "mock"})
                ),
            ]
            session.add_all(accounts)
            await session.commit()

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

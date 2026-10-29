from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import settings
from .models import AdminUser, Base

engine = create_async_engine(settings.DATABASE_URL, echo=settings.DEBUG, future=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    from .auth import hash_password

    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    if not settings.ADMIN_PASSWORD:
        return
    async with AsyncSessionLocal() as session:
        user = (await session.execute(select(AdminUser).where(
            AdminUser.email == settings.ADMIN_EMAIL.lower().strip()
        ))).scalar_one_or_none()
        if user is None:
            session.add(AdminUser(email=settings.ADMIN_EMAIL.lower().strip(),
                                  name=settings.ADMIN_NAME,
                                  password_hash=hash_password(settings.ADMIN_PASSWORD)))
            await session.commit()


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

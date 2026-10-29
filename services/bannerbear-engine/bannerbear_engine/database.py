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

    async with AsyncSessionLocal() as session:
        email = settings.ADMIN_EMAIL.lower().strip()
        user = (await session.execute(select(AdminUser).where(
            AdminUser.email == email
        ))).scalar_one_or_none()
        if user is not None:
            return

        # No baked default and no printed secret. If there is no admin account and no password
        # was supplied, fail loudly with instructions rather than inventing a credential or
        # writing one to a log. Existing installs with an admin row are unaffected.
        password = settings.ADMIN_PASSWORD
        if not password:
            raise RuntimeError(
                "No admin account exists and BANNERBEAR_ADMIN_PASSWORD is not set.\n"
                "Set it before the first start, for example:\n"
                "  export BANNERBEAR_ADMIN_PASSWORD='choose-a-long-unique-password'\n"
                "Only a PBKDF2 hash is stored. No default password ships with this service."
            )

        session.add(AdminUser(email=email, name=settings.ADMIN_NAME,
                              password_hash=hash_password(password)))
        await session.commit()


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

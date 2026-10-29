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

        # No baked default and no lockout: generate a unique password on first boot and print
        # it once. A known default on a network-bound service is a liability, and refusing to
        # start is worse than printing a secret into the operator's own console.
        password = settings.ADMIN_PASSWORD
        generated = False
        if not password:
            import secrets as _secrets

            password = _secrets.token_urlsafe(12)
            settings.ADMIN_PASSWORD = password
            generated = True

        session.add(AdminUser(email=email, name=settings.ADMIN_NAME,
                              password_hash=hash_password(password)))
        await session.commit()

        if generated:
            banner = "=" * 64
            print(banner)
            print("No BANNERBEAR_ADMIN_PASSWORD was set. Generated a unique one for this install.")
            print(f"  admin email:    {email}")
            print(f"  admin password: {password}")
            print("Shown once. Only its hash is stored. Save it, or set")
            print("BANNERBEAR_ADMIN_PASSWORD before first boot to choose your own.")
            print(banner, flush=True)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

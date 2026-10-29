from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import settings
from .models import AdminUser, Base

engine = create_async_engine(settings.DATABASE_URL, echo=settings.DEBUG, future=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    for statement in (
        "ALTER TABLE surveys ADD COLUMN webhook_url VARCHAR(2048)",
        "ALTER TABLE webhook_events ADD COLUMN delivery_status VARCHAR(20) NOT NULL DEFAULT 'pending'",
        "ALTER TABLE webhook_events ADD COLUMN response_code INTEGER",
        "ALTER TABLE webhook_events ADD COLUMN delivery_error VARCHAR(200)",
    ):
        try:
            async with engine.begin() as conn:
                await conn.exec_driver_sql(statement)
        except OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
    async with AsyncSessionLocal() as session:
        email = settings.ADMIN_EMAIL.lower().strip()
        user = (await session.execute(select(AdminUser).where(AdminUser.email == email))).scalar_one_or_none()
        if user is not None:
            return
        if not settings.ADMIN_PASSWORD:
            raise RuntimeError(
                "No admin account exists. Set ZIGPOLL_ADMIN_PASSWORD before first startup; "
                "the service does not generate, print, or store a default password."
            )
        from .auth import hash_password

        session.add(AdminUser(email=email, name=settings.ADMIN_NAME,
                              password_hash=hash_password(settings.ADMIN_PASSWORD)))
        await session.commit()


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

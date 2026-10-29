import asyncio
import secrets
import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bannerbear_engine.auth import token_hash
from bannerbear_engine.config import settings
from bannerbear_engine.database import AsyncSessionLocal, init_db
from bannerbear_engine.models import Project, ProjectAPIKey, Template


async def seed_demo() -> None:
    if not settings.ADMIN_PASSWORD:
        raise RuntimeError("Set BANNERBEAR_ADMIN_PASSWORD before seeding the demo")
    await init_db()
    async with AsyncSessionLocal() as db:
        project = (await db.execute(select(Project).where(Project.name == "Bannerbear Demo"))).scalar_one_or_none()
        if project is None:
            project = Project(name="Bannerbear Demo", credits=settings.DEMO_PROJECT_CREDITS)
            db.add(project)
            await db.commit()
            await db.refresh(project)
        templates = (await db.execute(select(Template).where(Template.project_id == project.id))).scalars().all()
        if not templates:
            db.add_all([
                Template(project_id=project.id, name="Midnight Quote", width=1080, height=1080,
                         background="#0c0e14", layers_json='[{"name":"title","type":"text","text":"Make it tangible.","x":92,"y":350,"font_size":74,"color":"#f0f4fc"},{"name":"caption","type":"text","text":"A real PNG, rendered locally.","x":98,"y":470,"font_size":34,"color":"#6ee7b7"}]'),
                Template(project_id=project.id, name="Product Portrait", width=1080, height=1350,
                         background="#18202b", layers_json='[{"name":"eyebrow","type":"text","text":"NEW ARRIVAL","x":84,"y":95,"font_size":28,"color":"#6ee7b7"},{"name":"product_name","type":"text","text":"Product name","x":84,"y":980,"font_size":62,"color":"#f0f4fc"},{"name":"description","type":"text","text":"A simple, editable product story.","x":88,"y":1070,"font_size":30,"color":"#c5d0d8"}]'),
            ])
            await db.commit()
        keys = (await db.execute(select(ProjectAPIKey).where(
            ProjectAPIKey.project_id == project.id, ProjectAPIKey.is_active.is_(True)
        ))).scalars().all()
        if not keys:
            secret = "bb_live_" + secrets.token_urlsafe(32)
            db.add(ProjectAPIKey(project_id=project.id, key_hash=token_hash(secret),
                                 prefix=secret[:15], label="seeded-demo"))
            await db.commit()


if __name__ == "__main__":
    asyncio.run(seed_demo())
    print("Seed complete: admin, 2 templates, demo project, and one project-scoped key are ready.")
    print("Open /admin and issue a fresh key for interactive use; key values are never printed by seed.")

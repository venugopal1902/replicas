from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Ctx, get_ctx
from ..common import contact_dict
from ..database import get_db
from ..models import Folder, FolderContact, Contact

router = APIRouter(prefix="/api/folders", tags=["folders"])


class FolderBody(BaseModel):
    name: str
    color: str | None = "#6ee7b7"


class FolderPatch(BaseModel):
    name: str | None = None
    color: str | None = None


class AddContacts(BaseModel):
    contactIds: list[str]


async def _count(db: AsyncSession, folder_id: str) -> int:
    return (await db.execute(
        select(func.count()).select_from(FolderContact).where(FolderContact.folder_id == folder_id)
    )).scalar() or 0


@router.get("")
async def list_folders(ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    folders = (await db.execute(
        select(Folder).where(Folder.workspace_id == ctx.workspace.id).order_by(Folder.created_at)
    )).scalars().all()
    items = []
    for f in folders:
        items.append({
            "id": f.id, "name": f.name, "color": f.color,
            "count": await _count(db, f.id),
            "createdAt": f.created_at.isoformat() if f.created_at else None,
        })
    return {"items": items}


@router.post("")
async def create_folder(body: FolderBody, ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    f = Folder(workspace_id=ctx.workspace.id, name=body.name.strip()[:160],
               color=body.color or "#6ee7b7", created_by=ctx.user.id)
    db.add(f)
    await db.commit()
    await db.refresh(f)
    return {"id": f.id, "name": f.name, "color": f.color, "count": 0,
            "createdAt": f.created_at.isoformat() if f.created_at else None}


@router.patch("/{folder_id}")
async def update_folder(folder_id: str, body: FolderPatch, ctx: Ctx = Depends(get_ctx),
                        db: AsyncSession = Depends(get_db)):
    f = (await db.execute(
        select(Folder).where(Folder.id == folder_id, Folder.workspace_id == ctx.workspace.id)
    )).scalar_one_or_none()
    if not f:
        raise HTTPException(status_code=404, detail="Folder not found")
    if body.name:
        f.name = body.name.strip()[:160]
    if body.color:
        f.color = body.color
    await db.commit()
    return {"id": f.id, "name": f.name, "color": f.color, "count": await _count(db, f.id)}


@router.delete("/{folder_id}")
async def delete_folder(folder_id: str, ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    f = (await db.execute(
        select(Folder).where(Folder.id == folder_id, Folder.workspace_id == ctx.workspace.id)
    )).scalar_one_or_none()
    if not f:
        raise HTTPException(status_code=404, detail="Folder not found")
    await db.execute(delete(FolderContact).where(FolderContact.folder_id == folder_id))
    await db.delete(f)
    await db.commit()
    return {"deleted": True}


@router.get("/{folder_id}/contacts")
async def folder_contacts(folder_id: str, ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    f = (await db.execute(
        select(Folder).where(Folder.id == folder_id, Folder.workspace_id == ctx.workspace.id)
    )).scalar_one_or_none()
    if not f:
        raise HTTPException(status_code=404, detail="Folder not found")
    rows = (await db.execute(
        select(Contact).join(FolderContact, FolderContact.contact_id == Contact.id)
        .where(FolderContact.folder_id == folder_id)
        .order_by(FolderContact.added_at.desc())
    )).scalars().all()
    return {"folder": {"id": f.id, "name": f.name, "color": f.color},
            "items": [contact_dict(c) for c in rows]}


@router.post("/{folder_id}/contacts")
async def add_contacts(folder_id: str, body: AddContacts, ctx: Ctx = Depends(get_ctx),
                       db: AsyncSession = Depends(get_db)):
    f = (await db.execute(
        select(Folder).where(Folder.id == folder_id, Folder.workspace_id == ctx.workspace.id)
    )).scalar_one_or_none()
    if not f:
        raise HTTPException(status_code=404, detail="Folder not found")

    existing = set((await db.execute(
        select(FolderContact.contact_id).where(FolderContact.folder_id == folder_id)
    )).scalars().all())

    added = 0
    for cid in body.contactIds[:1000]:
        if cid in existing:
            continue
        c = (await db.execute(
            select(Contact).where(Contact.id == cid, Contact.workspace_id == ctx.workspace.id)
        )).scalar_one_or_none()
        if not c:
            continue
        db.add(FolderContact(folder_id=folder_id, contact_id=cid, added_by=ctx.user.id))
        added += 1
    await db.commit()
    return {"added": added}


@router.delete("/{folder_id}/contacts/{contact_id}")
async def remove_contact(folder_id: str, contact_id: str, ctx: Ctx = Depends(get_ctx),
                         db: AsyncSession = Depends(get_db)):
    await db.execute(delete(FolderContact).where(
        FolderContact.folder_id == folder_id, FolderContact.contact_id == contact_id
    ))
    await db.commit()
    return {"removed": True}

import csv
import io

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Ctx, get_ctx
from ..database import get_db
from ..models import Lead, Suppression
from ..serializers import lead_dict

router = APIRouter(prefix="/api", tags=["leads"])


class LeadBody(BaseModel):
    email: str
    firstName: str | None = None
    lastName: str | None = None
    company: str | None = None
    title: str | None = None


class LeadPatch(BaseModel):
    firstName: str | None = None
    lastName: str | None = None
    company: str | None = None
    title: str | None = None
    status: str | None = None


class ImportBody(BaseModel):
    csv: str


def _clean_email(raw: str) -> str:
    return (raw or "").strip().strip("<>").lower()


def _valid_email(email: str) -> bool:
    if email.count("@") != 1:
        return False
    local, _, domain = email.partition("@")
    return bool(local) and "." in domain and " " not in email


HEADER_ALIASES = {
    "email": "email", "e-mail": "email", "work email": "email", "work_email": "email",
    "first_name": "firstName", "first name": "firstName", "firstname": "firstName",
    "last_name": "lastName", "last name": "lastName", "lastname": "lastName",
    "company": "company", "company name": "company",
    "title": "title", "job title": "title", "role": "title",
}


@router.get("/leads")
async def list_leads(status: str | None = None, q: str | None = None, limit: int = 500,
                     ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    query = select(Lead).where(Lead.workspace_id == ctx.workspace.id)
    if status:
        query = query.where(Lead.status == status)
    if q:
        needle = f"%{q.strip().lower()}%"
        query = query.where(or_(
            func.lower(Lead.email).like(needle),
            func.lower(Lead.first_name).like(needle),
            func.lower(Lead.last_name).like(needle),
            func.lower(Lead.company).like(needle),
        ))
    rows = (await db.execute(query.order_by(Lead.created_at.desc()).limit(max(1, min(limit, 2000))))).scalars().all()
    items = [lead_dict(l) for l in rows]
    total = (await db.execute(select(func.count(Lead.id)).where(
        Lead.workspace_id == ctx.workspace.id))).scalar_one()
    return {"items": items, "total": total, "returned": len(items)}


@router.post("/leads", status_code=201)
async def create_lead(body: LeadBody, ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    email = _clean_email(body.email)
    if not _valid_email(email):
        raise HTTPException(status_code=400, detail="A valid email address is required")
    existing = (await db.execute(select(Lead).where(
        Lead.workspace_id == ctx.workspace.id, Lead.email == email))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Lead already exists")
    lead = Lead(
        workspace_id=ctx.workspace.id,
        email=email,
        first_name=(body.firstName or "").strip() or None,
        last_name=(body.lastName or "").strip() or None,
        company=(body.company or "").strip() or None,
        title=(body.title or "").strip() or None,
        domain=email.split("@")[1] if "@" in email else None,
        status="new",
        source="manual",
    )
    db.add(lead)
    await db.commit()
    await db.refresh(lead)
    return {"lead": lead_dict(lead)}


@router.post("/leads/import")
async def import_leads(body: ImportBody, ctx: Ctx = Depends(get_ctx),
                       db: AsyncSession = Depends(get_db)):
    raw = (body.csv or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="CSV body is empty")

    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV needs a header row")

    mapping: dict[str, str] = {}
    for field in reader.fieldnames:
        key = HEADER_ALIASES.get((field or "").strip().lower())
        if key:
            mapping[field] = key
    if "email" not in mapping.values():
        raise HTTPException(status_code=400, detail="CSV needs an email column")

    existing = {row for row in (await db.execute(select(Lead.email).where(
        Lead.workspace_id == ctx.workspace.id))).scalars().all()}
    suppressed = {row for row in (await db.execute(select(Suppression.email).where(
        Suppression.workspace_id == ctx.workspace.id))).scalars().all()}

    imported, skipped, errors = 0, 0, []
    for line_no, row in enumerate(reader, start=2):
        values = {mapping[k]: (row.get(k) or "").strip() for k in mapping}
        email = _clean_email(values.get("email", ""))
        if not email:
            skipped += 1
            errors.append(f"row {line_no}: missing email")
            continue
        if not _valid_email(email):
            skipped += 1
            errors.append(f"row {line_no}: invalid email {email}")
            continue
        if email in existing:
            skipped += 1
            errors.append(f"row {line_no}: duplicate {email}")
            continue
        if email in suppressed:
            skipped += 1
            errors.append(f"row {line_no}: suppressed {email}")
            continue
        db.add(Lead(
            workspace_id=ctx.workspace.id,
            email=email,
            first_name=values.get("firstName") or None,
            last_name=values.get("lastName") or None,
            company=values.get("company") or None,
            title=values.get("title") or None,
            domain=email.split("@")[1],
            status="new",
            source="csv-import",
        ))
        existing.add(email)
        imported += 1
    await db.commit()
    return {"imported": imported, "skipped": skipped, "errors": errors[:20]}


@router.patch("/leads/{lead_id}")
async def patch_lead(lead_id: str, body: LeadPatch, ctx: Ctx = Depends(get_ctx),
                     db: AsyncSession = Depends(get_db)):
    lead = (await db.execute(select(Lead).where(
        Lead.id == lead_id, Lead.workspace_id == ctx.workspace.id))).scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    for field, attr in (("firstName", "first_name"), ("lastName", "last_name"),
                        ("company", "company"), ("title", "title"), ("status", "status")):
        value = getattr(body, field)
        if value is not None:
            setattr(lead, attr, value)
    await db.commit()
    await db.refresh(lead)
    return {"lead": lead_dict(lead)}


@router.delete("/leads/{lead_id}")
async def delete_lead(lead_id: str, ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    lead = (await db.execute(select(Lead).where(
        Lead.id == lead_id, Lead.workspace_id == ctx.workspace.id))).scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    await db.delete(lead)
    await db.commit()
    return {"ok": True, "deleted": lead_id}

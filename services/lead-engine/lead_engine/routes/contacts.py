from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, desc, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Ctx, get_ctx
from ..common import contact_dict, spend_credits
from ..config import settings
from ..database import get_db
from ..models import Contact
from .. import enrichment

router = APIRouter(prefix="/api", tags=["contacts"])


class CreateContact(BaseModel):
    firstName: str
    lastName: str | None = ""
    companyName: str | None = None
    domain: str | None = None
    title: str | None = None
    linkedinUrl: str | None = None
    location: str | None = None


class ImportBody(BaseModel):
    contacts: List[Dict[str, Any]]


class BulkReveal(BaseModel):
    contactIds: List[str]


def _seniority(title: Optional[str]) -> Optional[str]:
    if not title:
        return None
    t = title.lower()
    if "founder" in t or "ceo" in t or "cto" in t or "cfo" in t:
        return "founder" if "founder" in t or "ceo" in t else "c-level"
    if "vp" in t or "vice president" in t:
        return "vp"
    if "head of" in t:
        return "head"
    if "director" in t:
        return "director"
    if "manager" in t or "lead" in t:
        return "manager"
    return "ic"


async def _get_contact(db: AsyncSession, workspace_id: str, contact_id: str) -> Contact:
    c = (await db.execute(
        select(Contact).where(Contact.id == contact_id, Contact.workspace_id == workspace_id)
    )).scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Contact not found")
    return c


async def reveal_contact(db: AsyncSession, ctx: Ctx, contact: Contact) -> Dict[str, Any]:
    """Enrich one contact. Charges 1 credit only when an email is actually found."""
    if contact.revealed and (contact.work_email or contact.direct_email):
        return {"contact": contact_dict(contact, True), "creditsSpent": 0, "balance": ctx.workspace.credits}

    providers = {
        "hunter": settings.HUNTER_API_KEY,
        "apollo": settings.APOLLO_API_KEY,
        "pdl": settings.PDL_API_KEY,
    }
    try:
        result = await enrichment.enrich_person(
            contact.first_name or "",
            contact.last_name or "",
            company_name=contact.company_name,
            domain=contact.domain,
            linkedin_url=contact.linkedin_url,
            providers=providers,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Enrichment failed: {exc}")

    found = bool(result.get("workEmail") or result.get("directEmail"))
    contact.work_email = result.get("workEmail")
    contact.work_email_status = result.get("workEmailStatus")
    contact.work_email_score = result.get("workEmailScore")
    contact.work_email_pattern = result.get("workEmailPattern")
    contact.direct_email = result.get("directEmail")
    contact.direct_email_status = result.get("directEmailStatus")
    contact.direct_email_score = result.get("directEmailScore")
    contact.phone = result.get("phone")
    contact.phone_source = result.get("phoneSource")
    if not contact.domain and result.get("domain"):
        contact.domain = result["domain"]
    contact.revealed = True
    contact.enriched_at = datetime.utcnow()
    contact.source = contact.source or "database"

    spent = 0
    if found:
        try:
            await spend_credits(db, ctx.workspace, 1, "contact reveal",
                                pool="reveal", user_id=ctx.user.id, contact_id=contact.id)
            spent = 1
        except ValueError as exc:
            raise HTTPException(status_code=402, detail=str(exc))

    await db.commit()
    await db.refresh(contact)
    return {"contact": contact_dict(contact, True), "creditsSpent": spent,
            "balance": ctx.workspace.credits}


@router.get("/contacts")
async def list_contacts(
    q: str | None = None,
    folderId: str | None = None,
    page: int = 1,
    pageSize: int = 20,
    ctx: Ctx = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
):
    from ..models import FolderContact

    query = select(Contact).where(Contact.workspace_id == ctx.workspace.id)
    if folderId:
        sub = select(FolderContact.contact_id).where(FolderContact.folder_id == folderId)
        query = query.where(Contact.id.in_(sub))
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(
            Contact.first_name.ilike(like),
            Contact.last_name.ilike(like),
            Contact.company_name.ilike(like),
            Contact.title.ilike(like),
            Contact.work_email.ilike(like),
        ))

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    page = max(page, 1)
    page_size = min(max(pageSize, 1), 100)
    rows = (await db.execute(
        query.order_by(desc(Contact.created_at)).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()

    return {"total": total, "page": page, "pageSize": page_size,
            "items": [contact_dict(c) for c in rows]}


@router.get("/contacts/{contact_id}")
async def get_contact(contact_id: str, ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    c = await _get_contact(db, ctx.workspace.id, contact_id)
    return contact_dict(c)


@router.post("/contacts")
async def create_contact(body: CreateContact, ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    domain = body.domain
    if not domain:
        try:
            domain = await enrichment.resolve_domain(body.companyName or "")
        except Exception:  # noqa: BLE001
            domain = None

    c = Contact(
        workspace_id=ctx.workspace.id,
        first_name=body.firstName,
        last_name=body.lastName or "",
        title=body.title,
        seniority=_seniority(body.title),
        company_name=body.companyName,
        domain=domain,
        linkedin_url=body.linkedinUrl,
        location=body.location,
        source="manual",
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return contact_dict(c)


@router.delete("/contacts/{contact_id}")
async def delete_contact(contact_id: str, ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    c = await _get_contact(db, ctx.workspace.id, contact_id)
    await db.delete(c)
    await db.commit()
    return {"deleted": True}


@router.post("/contacts/import")
async def import_contacts(body: ImportBody, ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    created = 0
    for row in body.contacts[:5000]:
        first = (row.get("firstName") or row.get("first_name") or "").strip()
        last = (row.get("lastName") or row.get("last_name") or "").strip()
        if not first and not last:
            continue
        db.add(Contact(
            workspace_id=ctx.workspace.id,
            first_name=first,
            last_name=last,
            title=row.get("title"),
            seniority=_seniority(row.get("title")),
            company_name=row.get("companyName") or row.get("company"),
            domain=row.get("domain"),
            linkedin_url=row.get("linkedinUrl") or row.get("linkedin_url"),
            location=row.get("location"),
            source="csv",
        ))
        created += 1
    await db.commit()
    return {"imported": created}


@router.post("/contacts/{contact_id}/reveal")
async def reveal_one(contact_id: str, ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    c = await _get_contact(db, ctx.workspace.id, contact_id)
    return await reveal_contact(db, ctx, c)


@router.post("/contacts/reveal-bulk")
async def reveal_bulk(body: BulkReveal, ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    results = []
    spent_total = 0
    for cid in body.contactIds[:100]:
        try:
            c = await _get_contact(db, ctx.workspace.id, cid)
            out = await reveal_contact(db, ctx, c)
            spent_total += out["creditsSpent"]
            results.append({"contactId": cid, "ok": True,
                            "found": bool(c.work_email or c.direct_email)})
        except HTTPException as exc:
            results.append({"contactId": cid, "ok": False, "error": exc.detail})
        except Exception as exc:  # noqa: BLE001
            results.append({"contactId": cid, "ok": False, "error": str(exc)})
    return {"results": results, "creditsSpent": spent_total, "balance": ctx.workspace.credits}

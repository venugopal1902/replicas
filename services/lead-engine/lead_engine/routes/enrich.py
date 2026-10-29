"""Enrichment jobs: LinkedIn URLs, identity, reverse email and CSV."""
import asyncio
import csv
import io
import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select, desc, or_
from sqlalchemy.ext.asyncio import AsyncSession

from .. import enrichment
from ..auth import Ctx, get_ctx
from ..common import contact_dict, spend_credits
from ..config import settings
from ..database import AsyncSessionLocal, get_db
from ..models import Contact, EnrichmentJob, Workspace

router = APIRouter(prefix="/api", tags=["enrich"])

MAX_CSV_ROWS = 5000


class LinkedInBody(BaseModel):
    urls: List[str]
    companyHint: Optional[str] = None
    folderId: Optional[str] = None


class IdentityBody(BaseModel):
    firstName: str
    lastName: str = ""
    companyName: Optional[str] = None
    domain: Optional[str] = None
    linkedinUrl: Optional[str] = None
    title: Optional[str] = None


class ReverseEmailBody(BaseModel):
    email: str


def _job_dict(j: EnrichmentJob) -> Dict[str, Any]:
    return {
        "id": j.id,
        "jobType": j.job_type,
        "status": j.status,
        "total": j.total,
        "processed": j.processed,
        "found": j.found,
        "creditsSpent": j.credits_spent,
        "error": j.error,
        "downloadUrl": f"/api/jobs/{j.id}/download" if j.output_path else None,
        "createdAt": j.created_at.isoformat() if j.created_at else None,
        "finishedAt": j.finished_at.isoformat() if j.finished_at else None,
    }


def providers_for(workspace: Optional[Workspace] = None) -> Dict[str, str]:
    hunter = (getattr(workspace, "hunter_key", None) if workspace else None) or settings.HUNTER_API_KEY or ""
    apollo = (getattr(workspace, "apollo_key", None) if workspace else None) or settings.APOLLO_API_KEY or ""
    pdl = (getattr(workspace, "pdl_key", None) if workspace else None) or settings.PDL_API_KEY or ""
    return {
        "hunter": hunter,
        "apollo": apollo,
        "pdl": pdl,
    }


def _providers(workspace: Optional[Workspace] = None) -> Dict[str, str]:
    return providers_for(workspace)


async def _enrich_one(session: AsyncSession, workspace: Workspace, contact: Contact) -> Dict[str, Any]:
    """Enrich a single contact and return a flat row. Charges only on a found email."""
    result = await enrichment.enrich_person(
        contact.first_name or "",
        contact.last_name or "",
        company_name=contact.company_name,
        domain=contact.domain,
        linkedin_url=contact.linkedin_url,
        providers=providers_for(workspace),
    )

    found = bool(result.get("workEmail") or result.get("directEmail"))
    contact.work_email = result.get("workEmail") or contact.work_email
    contact.work_email_status = result.get("workEmailStatus")
    contact.work_email_score = result.get("workEmailScore")
    contact.work_email_pattern = result.get("workEmailPattern")
    contact.direct_email = result.get("directEmail") or contact.direct_email
    contact.direct_email_status = result.get("directEmailStatus")
    contact.direct_email_score = result.get("directEmailScore")
    contact.phone = result.get("phone") or contact.phone
    contact.phone_source = result.get("phoneSource")
    if not contact.domain and result.get("domain"):
        contact.domain = result["domain"]
    contact.revealed = True
    contact.enriched_at = datetime.utcnow()

    spent = 0
    if found:
        try:
            await spend_credits(session, workspace, 1, "bulk enrichment", contact_id=contact.id)
            spent = 1
        except ValueError:
            spent = 0  # out of credits: still return what we found, charge nothing

    return {
        "contact": contact,
        "found": found,
        "credits": spent,
        "outcome": "found" if found else "not_found",
    }


def _row_from_contact(c: Contact, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    row = dict(extra or {})
    row.update({
        "first_name": c.first_name or "",
        "last_name": c.last_name or "",
        "company": c.company_name or "",
        "domain": c.domain or "",
        "title": c.title or "",
        "linkedin_url": c.linkedin_url or "",
        "work_email": c.work_email or "",
        "work_email_status": c.work_email_status or "",
        "work_email_score": c.work_email_score or "",
        "direct_email": c.direct_email or "",
        "direct_email_status": c.direct_email_status or "",
        "phone": c.phone or "",
        "phone_source": c.phone_source or "",
    })
    return row


async def _write_csv(path: Path, rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("first_name,last_name,company,work_email\n", encoding="utf-8")
        return
    headers: List[str] = []
    for r in rows:
        for k in r.keys():
            if k not in headers:
                headers.append(k)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    path.write_text(buf.getvalue(), encoding="utf-8")


async def _run_job(job_id: str) -> None:
    """Background worker. Opens its own session so the request can return immediately."""
    async with AsyncSessionLocal() as session:
        job = (await session.execute(select(EnrichmentJob).where(EnrichmentJob.id == job_id))).scalar_one_or_none()
        if not job:
            return
        workspace = (await session.execute(
            select(Workspace).where(Workspace.id == job.workspace_id)
        )).scalar_one_or_none()
        if not workspace:
            job.status = "failed"
            job.error = "Workspace missing"
            await session.commit()
            return

        job.status = "running"
        await session.commit()

        payload: Dict[str, Any] = json.loads(job.input_path)
        rows: List[Dict[str, str]] = []

        try:
            if job.job_type == "linkedin_urls":
                for url in payload.get("urls", []):
                    parsed = enrichment.parse_linkedin_url(url)
                    contact = None
                    if parsed.get("is_valid"):
                        contact = (await session.execute(
                            select(Contact).where(
                                Contact.workspace_id == workspace.id,
                                Contact.linkedin_url.ilike(f"%{parsed['handle']}%"),
                            )
                        )).scalars().first()

                    if not contact and parsed.get("name_guess"):
                        parts = parsed["name_guess"].split(" ", 1)
                        if len(parts) == 2:
                            contact = (await session.execute(
                                select(Contact).where(
                                    Contact.workspace_id == workspace.id,
                                    Contact.first_name.ilike(parts[0]),
                                    Contact.last_name.ilike(parts[1]),
                                )
                            )).scalars().first()

                    if not contact and parsed.get("is_valid"):
                        parts = (parsed.get("name_guess") or "").split(" ", 1)
                        contact = Contact(
                            workspace_id=workspace.id,
                            first_name=parts[0] if parts else "",
                            last_name=parts[1] if len(parts) > 1 else "",
                            company_name=payload.get("companyHint"),
                            linkedin_url=url,
                            source="linkedin",
                        )
                        session.add(contact)
                        await session.commit()
                        await session.refresh(contact)

                    if not contact:
                        rows.append({"linkedin_url": url, "outcome": "invalid_url"})
                        job.processed += 1
                        continue

                    if not contact.company_name and not contact.domain:
                        rows.append(_row_from_contact(contact, {"outcome": "needs_company"}))
                    else:
                        res = await _enrich_one(session, workspace, contact)
                        job.found += 1 if res["found"] else 0
                        job.credits_spent += res["credits"]
                        rows.append(_row_from_contact(contact, {"outcome": res["outcome"]}))

                    job.processed += 1
                    await session.commit()

            elif job.job_type in ("csv_url", "csv_lead", "csv_email"):
                mode = job.job_type.replace("csv_", "")
                for raw in payload.get("rows", []):
                    row = {k: (v or "") for k, v in raw.items()}

                    contact = None
                    if mode == "url":
                        url = next((v for k, v in row.items() if "linkedin" in k.lower() or "url" in k.lower()), "")
                        parsed = enrichment.parse_linkedin_url(url)
                        if parsed.get("is_valid"):
                            contact = (await session.execute(
                                select(Contact).where(
                                    Contact.workspace_id == workspace.id,
                                    Contact.linkedin_url.ilike(f"%{parsed['handle']}%"),
                                )
                            )).scalars().first()
                            if not contact:
                                parts = (parsed.get("name_guess") or "").split(" ", 1)
                                contact = Contact(
                                    workspace_id=workspace.id,
                                    first_name=parts[0] if parts else "",
                                    last_name=parts[1] if len(parts) > 1 else "",
                                    company_name=row.get("company") or row.get("Company") or None,
                                    domain=None,
                                    linkedin_url=url,
                                    source="csv",
                                )
                                session.add(contact)
                                await session.commit()
                                await session.refresh(contact)

                    elif mode == "lead":
                        first = row.get("first_name") or row.get("First Name") or row.get("firstName") or ""
                        last = row.get("last_name") or row.get("Last Name") or row.get("lastName") or ""
                        company = row.get("company") or row.get("Company") or row.get("Company Name") or ""
                        if first or last:
                            contact = Contact(
                                workspace_id=workspace.id,
                                first_name=first.strip(),
                                last_name=last.strip(),
                                company_name=company.strip() or None,
                                source="csv",
                            )
                            session.add(contact)
                            await session.commit()
                            await session.refresh(contact)

                    else:  # email reverse lookup
                        email = next((v for k, v in row.items() if "email" in k.lower()), "")
                        if email:
                            contact = (await session.execute(
                                select(Contact).where(
                                    Contact.workspace_id == workspace.id,
                                    or_(Contact.work_email == email, Contact.direct_email == email),
                                )
                            )).scalars().first()

                    if not contact:
                        rows.append({**row, "outcome": "not_found"})
                        job.processed += 1
                        await session.commit()
                        continue

                    if not (contact.company_name or contact.domain) and mode != "email":
                        rows.append(_row_from_contact(contact, {**row, "outcome": "needs_company"}))
                    else:
                        res = await _enrich_one(session, workspace, contact)
                        job.found += 1 if res["found"] else 0
                        job.credits_spent += res["credits"]
                        rows.append(_row_from_contact(contact, {**row, "outcome": res["outcome"]}))

                    job.processed += 1
                    await session.commit()

            out_path = settings.DATA_DIR / "jobs" / f"{job.id}.csv"
            await _write_csv(out_path, rows)
            job.output_path = str(out_path)
            job.rows_json = json.dumps(rows[:500])
            job.status = "done"
            job.finished_at = datetime.utcnow()

        except Exception as exc:  # noqa: BLE001
            job.status = "failed"
            job.error = str(exc)
            if rows:
                job.rows_json = json.dumps(rows[:500])
            job.finished_at = datetime.utcnow()

        await session.commit()


async def _new_job(session: AsyncSession, ctx: Ctx, job_type: str, payload: Dict[str, Any]) -> EnrichmentJob:
    total = len(payload.get("urls", [])) or len(payload.get("rows", []))
    job = EnrichmentJob(
        workspace_id=ctx.workspace.id,
        user_id=ctx.user.id,
        job_type=job_type,
        status="queued",
        total=total,
        input_path=json.dumps(payload),
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    asyncio.create_task(_run_job(job.id))
    return job


@router.post("/enrich/linkedin")
async def enrich_linkedin(body: LinkedInBody, ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    if not body.urls:
        raise HTTPException(status_code=400, detail="Provide at least one LinkedIn URL")
    if len(body.urls) > 1000:
        raise HTTPException(status_code=400, detail="Maximum 1000 URLs per job")
    job = await _new_job(db, ctx, "linkedin_urls", {
        "urls": body.urls,
        "companyHint": body.companyHint,
        "folderId": body.folderId,
    })
    return _job_dict(job)


@router.post("/enrich/identity")
async def enrich_identity(body: IdentityBody, ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    domain = body.domain
    if not domain and body.companyName:
        domain = await enrichment.resolve_domain(body.companyName)

    contact = Contact(
        workspace_id=ctx.workspace.id,
        first_name=body.firstName,
        last_name=body.lastName,
        title=body.title,
        company_name=body.companyName,
        domain=domain,
        linkedin_url=body.linkedinUrl,
        source="api",
    )
    db.add(contact)
    await db.commit()
    await db.refresh(contact)

    res = await _enrich_one(db, ctx.workspace, contact)
    await db.commit()
    await db.refresh(contact)

    return {
        "contact": contact_dict(contact, True),
        "creditsSpent": res["credits"],
        "balance": ctx.workspace.credits,
    }


@router.post("/enrich/reverse-email")
async def reverse_email(body: ReverseEmailBody, ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    email = body.email.strip().lower()
    contact = (await db.execute(
        select(Contact).where(
            Contact.workspace_id == ctx.workspace.id,
            or_(Contact.work_email == email, Contact.direct_email == email),
        )
    )).scalars().first()

    if not contact:
        return {"contact": None, "creditsSpent": 0,
                "note": "Not in this workspace. A provider key (Hunter/Apollo/PDL) is required "
                        "to resolve an unknown address into a person."}

    return {"contact": contact_dict(contact, True), "creditsSpent": 0}


@router.get("/jobs")
async def list_jobs(limit: int = Query(30, ge=1, le=100), ctx: Ctx = Depends(get_ctx),
                    db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(EnrichmentJob).where(EnrichmentJob.workspace_id == ctx.workspace.id)
        .order_by(desc(EnrichmentJob.created_at)).limit(limit)
    )).scalars().all()
    return {"items": [_job_dict(j) for j in rows]}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    job = (await db.execute(
        select(EnrichmentJob).where(EnrichmentJob.id == job_id,
                                    EnrichmentJob.workspace_id == ctx.workspace.id)
    )).scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_dict(job)


@router.get("/jobs/{job_id}/rows")
async def get_job_rows(job_id: str, ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    job = (await db.execute(
        select(EnrichmentJob).where(EnrichmentJob.id == job_id,
                                    EnrichmentJob.workspace_id == ctx.workspace.id)
    )).scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    items: List[Dict[str, Any]] = []
    if job.rows_json:
        try:
            items = json.loads(job.rows_json)
        except Exception:
            items = []
    elif job.output_path and Path(job.output_path).exists():
        try:
            with open(job.output_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                items = [row for i, row in enumerate(reader) if i < 500]
        except Exception:
            items = []
    return {"items": items, "total": len(items)}


@router.get("/jobs/{job_id}/download")
async def download_job(job_id: str, ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    job = (await db.execute(
        select(EnrichmentJob).where(EnrichmentJob.id == job_id,
                                    EnrichmentJob.workspace_id == ctx.workspace.id)
    )).scalar_one_or_none()
    if not job or not job.output_path:
        raise HTTPException(status_code=404, detail="No result file for this job")
    path = Path(job.output_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Result file missing on disk")
    return FileResponse(path, media_type="text/csv", filename=f"lead-engine-{job.job_type}-{job.id[:8]}.csv")


@router.post("/csv/enrich")
async def csv_enrich(
    file: UploadFile = File(...),
    mode: str = Form("url"),
    folderId: str | None = Form(None),
    ctx: Ctx = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
):
    if mode not in ("url", "lead", "email"):
        raise HTTPException(status_code=400, detail="mode must be url, lead or email")

    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for i, row in enumerate(reader):
        if i >= MAX_CSV_ROWS:
            break
        rows.append({k: (v or "") for k, v in row.items() if k is not None})

    if not rows:
        raise HTTPException(status_code=400, detail="CSV has no data rows")

    job = await _new_job(db, ctx, f"csv_{mode}", {"rows": rows, "folderId": folderId})
    return _job_dict(job)


@router.get("/csv/template")
async def csv_template(mode: str = Query("url")):
    if mode == "lead":
        content = "first_name,last_name,company\nMaya,Okafor,Northvale\nArjun,Mehta,Kestrelpay\n"
    elif mode == "email":
        content = "email\nmaya.okafor@northvale.io\narjun@kestrelpay.com\n"
    else:
        content = "linkedin_url,company\nhttps://www.linkedin.com/in/maya-okafor-1a2b,Northvale\n"
    return PlainTextResponse(content, media_type="text/csv")

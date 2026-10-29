"""Shared helpers: contact serialisation, masking, credit spending."""
import json
import re
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Contact, CreditEvent, Workspace

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def mask_email(email: Optional[str]) -> Optional[str]:
    """a***@domain.com - enough to show a domain without giving the address away."""
    if not email or "@" not in email:
        return None
    local, domain = email.split("@", 1)
    if len(local) <= 1:
        return f"*@{domain}"
    return f"{local[0]}***@{domain}"


def contact_dict(c: Contact, revealed: Optional[bool] = None) -> Dict[str, Any]:
    """Full contact shape. Masks emails unless the contact is revealed."""
    show = c.revealed if revealed is None else revealed
    name = " ".join(p for p in [c.first_name, c.last_name] if p) or "Unknown"
    data = {
        "id": c.id,
        "name": name,
        "firstName": c.first_name,
        "lastName": c.last_name,
        "title": c.title,
        "seniority": c.seniority,
        "companyName": c.company_name,
        "domain": c.domain,
        "industry": c.industry,
        "headcount": c.headcount,
        "location": c.location,
        "linkedinUrl": c.linkedin_url,
        "yearsExperience": c.years_experience,
        "revealed": bool(c.revealed),
        "workEmailMasked": mask_email(c.work_email),
        "directEmailMasked": mask_email(c.direct_email),
        "hasPhone": bool(c.phone),
        "source": c.source,
        "enrichedAt": c.enriched_at.isoformat() if c.enriched_at else None,
        "createdAt": c.created_at.isoformat() if c.created_at else None,
    }
    if show:
        data.update({
            "workEmail": c.work_email,
            "workEmailStatus": c.work_email_status or ("unavailable" if not c.work_email else "unknown"),
            "workEmailScore": c.work_email_score,
            "workEmailPattern": c.work_email_pattern,
            "directEmail": c.direct_email,
            "directEmailStatus": c.direct_email_status or ("unavailable" if not c.direct_email else "unknown"),
            "directEmailScore": c.direct_email_score,
            "phone": c.phone,
            "phoneSource": c.phone_source,
        })
    else:
        data.update({
            "workEmail": mask_email(c.work_email),
            "workEmailStatus": None,
            "workEmailScore": None,
            "workEmailPattern": None,
            "directEmail": mask_email(c.direct_email),
            "directEmailStatus": None,
            "directEmailScore": None,
            "phone": None,
            "phoneSource": None,
        })
    return data


async def spend_credits(
    db: AsyncSession,
    workspace: Workspace,
    amount: int,
    reason: str,
    *,
    pool: str = "reveal",
    user_id: Optional[str] = None,
    contact_id: Optional[str] = None,
) -> int:
    """Deduct from a credit pool. Returns the new balance. Raises ValueError when short."""
    if amount <= 0:
        return workspace.credits if pool == "reveal" else workspace.verifier_credits

    field = "credits" if pool == "reveal" else "verifier_credits"
    balance = getattr(workspace, field) or 0
    if balance < amount:
        raise ValueError(f"Not enough {pool} credits: have {balance}, need {amount}")
    setattr(workspace, field, balance - amount)

    db.add(CreditEvent(
        workspace_id=workspace.id,
        user_id=user_id,
        delta=-amount,
        pool=pool,
        reason=reason,
        contact_id=contact_id,
        balance_after=getattr(workspace, field),
    ))
    return getattr(workspace, field)


def parse_filters(prompt: Optional[str], filters: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge a natural-language prompt into structured filters."""
    out = dict(filters or {})
    if not prompt:
        return out

    text = prompt.strip()
    lowered = text.lower()

    # cheap, predictable extraction - no LLM required
    for loc in ["london", "berlin", "amsterdam", "dublin", "manchester", "edinburgh",
                "san francisco", "new york", "paris", "remote"]:
        if loc in lowered and "location" not in out:
            out["location"] = loc.title()

    for hc, label in [("11-50", ["11-50", "11–50"]), ("51-200", ["51-200", "51–200"]),
                      ("201-500", ["201-500", "201–500"]), ("1000+", ["1000+", "1000+"])]:
        if any(h in lowered for h in label) and "headcount" not in out:
            out["headcount"] = hc

    for ind in ["fintech", "saas", "consulting", "design", "cloud", "healthcare", "e-commerce"]:
        if ind in lowered and "industry" not in out:
            out["industry"] = ind.title()

    if "head of" in lowered and "title" not in out:
        out["title"] = "Head of"
    if "founder" in lowered and "title" not in out:
        out["title"] = "Founder"
    if "vp " in lowered or "vice president" in lowered:
        out.setdefault("seniority", "vp")

    return out

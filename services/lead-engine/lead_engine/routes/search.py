import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import and_, case, distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Ctx, get_ctx
from ..common import contact_dict, parse_filters
from ..database import get_db
from ..models import Contact, SearchLog

router = APIRouter(prefix="/api", tags=["search"])


class SearchBody(BaseModel):
    prompt: Optional[str] = None
    q: Optional[str] = None
    filters: Optional[Dict[str, Any]] = None
    page: int = 1
    pageSize: int = 20


def _word_match(field, term: str):
    """Match a term only at the start of a word, not anywhere inside one.

    ``%venu%`` matched "VP Revenue" because "Revenue" contains "venu". A recruiter typing a name
    wants "Venu Gopal", not every title with those letters buried in it.
    """
    safe = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return or_(
        field.ilike(f"{safe}%"),
        field.ilike(f"% {safe}%"),
        field.ilike(f"%-{safe}%"),
        field.ilike(f"%/{safe}%"),
        field.ilike(f"%({safe}%"),
        field.ilike(f"%'{safe}%"),
    )


def _match_all_tokens(columns, text: str):
    """Every whitespace-separated token must match at least one column."""
    conditions = []
    for token in [t for t in text.split() if t]:
        conditions.append(or_(*[_word_match(column, token) for column in columns]))
    return and_(*conditions) if conditions else None


@router.post("/search")
async def search(body: SearchBody, ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    filters = parse_filters(body.prompt, body.filters)

    query = select(Contact).where(Contact.workspace_id == ctx.workspace.id)

    def like(field, value):
        return _word_match(field, value)

    # Free-text match across the fields a recruiter actually types: a name, a company,
    # a title, a domain or a LinkedIn slug. Without this the box silently returned everything.
    term = (body.q or "").strip()
    if term:
        condition = _match_all_tokens(
            [Contact.first_name, Contact.last_name, Contact.company_name, Contact.title,
             Contact.domain, Contact.location, Contact.linkedin_url, Contact.work_email],
            term,
        )
        if condition is not None:
            query = query.where(condition)

    if filters.get("title"):
        query = query.where(like(Contact.title, filters["title"]))
    if filters.get("company"):
        query = query.where(like(Contact.company_name, filters["company"]))
    if filters.get("industry"):
        query = query.where(like(Contact.industry, filters["industry"]))
    if filters.get("location"):
        query = query.where(like(Contact.location, filters["location"]))
    if filters.get("headcount"):
        query = query.where(Contact.headcount == filters["headcount"])
    if filters.get("seniority"):
        query = query.where(Contact.seniority == filters["seniority"])
    if filters.get("hasDirectEmail"):
        query = query.where(Contact.direct_email.isnot(None))
    if filters.get("hasPhone"):
        query = query.where(Contact.phone.isnot(None))
    if filters.get("revealed") is not None:
        query = query.where(Contact.revealed == bool(filters["revealed"]))

    # Rank a name hit above a title hit, so typing a person's name finds the person.
    rank_clause = Contact.company_name
    if term:
        first_token = term.split()[0].replace("%", "").replace("_", "")
        rank_clause = case(
            (Contact.first_name.ilike(f"{first_token}%"), 0),
            (Contact.last_name.ilike(f"{first_token}%"), 1),
            (Contact.company_name.ilike(f"{first_token}%"), 2),
            (Contact.title.ilike(f"{first_token}%"), 3),
            else_=4,
        )

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0

    # A term typed into one filter box that matches nothing there is usually a person's name typed
    # in the wrong box. Rather than showing an empty screen, look in every field and say so.
    note = None
    if total == 0:
        text_fields = ("title", "company", "industry", "location")
        loose = [str(v).strip() for k, v in (filters or {}).items()
                 if isinstance(v, str) and v.strip() and k in text_fields]
        if loose and not term:
            text = " ".join(loose)
            condition = _match_all_tokens(
                [Contact.first_name, Contact.last_name, Contact.company_name, Contact.title,
                 Contact.domain, Contact.location, Contact.linkedin_url, Contact.work_email],
                text,
            )
            if condition is not None:
                fallback = select(Contact).where(Contact.workspace_id == ctx.workspace.id).where(condition)
                fallback_total = (await db.execute(
                    select(func.count()).select_from(fallback.subquery()))).scalar() or 0
                if fallback_total:
                    query = fallback
                    total = fallback_total
                    used = ", ".join(sorted(k for k in text_fields
                                            if isinstance((filters or {}).get(k), str)
                                            and str((filters or {}).get(k)).strip()))
                    note = f"No {used} matched \"{text}\". Showing matches from any field."

    page = max(int(body.page or 1), 1)
    page_size = min(max(int(body.pageSize or 20), 1), 100)
    rows = (await db.execute(
        query.order_by(rank_clause, Contact.revealed.desc(), Contact.company_name, Contact.last_name)
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()

    db.add(SearchLog(
        workspace_id=ctx.workspace.id,
        user_id=ctx.user.id,
        prompt=body.prompt,
        filters_json=json.dumps(filters),
        result_count=total,
    ))
    await db.commit()

    return {
        "total": total,
        "page": page,
        "pageSize": page_size,
        "filtersApplied": filters,
        "note": note,
        "creditsCharged": 0,
        "items": [
            {
                "id": c.id,
                "name": contact_dict(c)["name"],
                "firstName": c.first_name,
                "lastName": c.last_name,
                "title": c.title,
                "seniority": c.seniority,
                "companyName": c.company_name,
                "industry": c.industry,
                "headcount": c.headcount,
                "location": c.location,
                "linkedinUrl": c.linkedin_url,
                "domain": c.domain,
                "revealed": bool(c.revealed),
                "workEmailMasked": contact_dict(c)["workEmailMasked"],
                "directEmailMasked": contact_dict(c)["directEmailMasked"],
                "hasDirectEmail": bool(c.direct_email),
                "hasPhone": bool(c.phone),
            }
            for c in rows
        ],
    }


@router.get("/search/suggestions")
async def suggestions(ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    async def distinct_values(col) -> List[str]:
        rows = (await db.execute(
            select(distinct(col)).where(Contact.workspace_id == ctx.workspace.id, col.isnot(None))
            .order_by(col).limit(80)
        )).scalars().all()
        return [r for r in rows if r]

    return {
        "titles": await distinct_values(Contact.title),
        "companies": await distinct_values(Contact.company_name),
        "industries": await distinct_values(Contact.industry),
        "locations": await distinct_values(Contact.location),
        "headcounts": ["11-50", "51-200", "201-500", "501-1000", "1000+"],
        "seniorities": ["founder", "c-level", "vp", "head", "director", "manager", "ic"],
    }

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, func, or_, distinct
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Ctx, get_ctx
from ..common import contact_dict, parse_filters
from ..database import get_db
from ..models import Contact, SearchLog

router = APIRouter(prefix="/api", tags=["search"])


class SearchBody(BaseModel):
    prompt: Optional[str] = None
    filters: Optional[Dict[str, Any]] = None
    page: int = 1
    pageSize: int = 20


@router.post("/search")
async def search(body: SearchBody, ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    filters = parse_filters(body.prompt, body.filters)

    query = select(Contact).where(Contact.workspace_id == ctx.workspace.id)

    def like(field, value):
        return field.ilike(f"%{value}%")

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

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0

    page = max(int(body.page or 1), 1)
    page_size = min(max(int(body.pageSize or 20), 1), 100)
    rows = (await db.execute(
        query.order_by(Contact.revealed.desc(), Contact.company_name, Contact.last_name)
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
                "revealed": bool(c.revealed),
                "workEmailMasked": contact_dict(c)["workEmailMasked"],
                "directEmailMasked": contact_dict(c)["directEmailMasked"],
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

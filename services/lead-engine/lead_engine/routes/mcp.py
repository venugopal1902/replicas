"""JSON-RPC MCP server so agents can prospect and enrich from a chat."""
import json
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from .. import enrichment
from ..auth import Ctx, get_ctx
from ..common import contact_dict, parse_filters
from ..config import settings
from ..database import get_db
from ..models import Contact, Folder, FolderContact
from .contacts import reveal_contact

router = APIRouter(tags=["mcp"])


def _tool(name: str, desc: str, props: Dict[str, Any], required: List[str] | None = None) -> Dict[str, Any]:
    return {"name": name, "description": desc,
            "inputSchema": {"type": "object", "properties": props, "required": required or []}}


TOOLS = [
    _tool("search_prospects",
          "Search the contact index by title, company, industry, location, headcount or a natural-language prompt. Searching costs no credits.",
          {"prompt": {"type": "string"},
           "filters": {"type": "object"},
           "page": {"type": "number", "default": 1},
           "pageSize": {"type": "number", "default": 20}}),
    _tool("reveal_contact",
          "Reveal a contact's verified work email, direct email and phone. Costs 1 credit only when an email is found.",
          {"contactId": {"type": "string"}}, ["contactId"]),
    _tool("enrich_linkedin",
          "Enrich a person from a LinkedIn URL plus a company hint, returning their work email.",
          {"linkedinUrl": {"type": "string"}, "company": {"type": "string"}},
          ["linkedinUrl"]),
    _tool("verify_email",
          "Check whether an email address is deliverable. Returns valid, invalid, risky or catch_all.",
          {"email": {"type": "string"}}, ["email"]),
    _tool("list_folders", "List saved contact folders in this workspace.", {}),
    _tool("check_credits", "Report remaining reveal and verifier credits.", {}),
]


def _text(payload: Any) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, default=str)}]}


async def _dispatch(name: str, args: Dict[str, Any], ctx: Ctx, db: AsyncSession) -> Dict[str, Any]:
    if name == "search_prospects":
        filters = parse_filters(args.get("prompt"), args.get("filters"))
        query = select(Contact).where(Contact.workspace_id == ctx.workspace.id)
        if filters.get("title"):
            query = query.where(Contact.title.ilike(f"%{filters['title']}%"))
        if filters.get("company"):
            query = query.where(Contact.company_name.ilike(f"%{filters['company']}%"))
        if filters.get("industry"):
            query = query.where(Contact.industry.ilike(f"%{filters['industry']}%"))
        if filters.get("location"):
            query = query.where(Contact.location.ilike(f"%{filters['location']}%"))
        if filters.get("headcount"):
            query = query.where(Contact.headcount == filters["headcount"])

        total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
        page_size = min(max(int(args.get("pageSize", 20)), 1), 50)
        rows = (await db.execute(
            query.order_by(Contact.revealed.desc(), Contact.company_name)
            .limit(page_size)
        )).scalars().all()

        return _text({
            "total": total,
            "creditsUsed": 0,
            "items": [
                {"id": c.id, "name": f"{c.first_name} {c.last_name}".strip(),
                 "title": c.title, "company": c.company_name, "location": c.location,
                 "workEmailMasked": contact_dict(c)["workEmailMasked"],
                 "directEmailMasked": contact_dict(c)["directEmailMasked"],
                 "revealed": bool(c.revealed)}
                for c in rows
            ],
        })

    if name == "reveal_contact":
        contact = (await db.execute(
            select(Contact).where(Contact.id == args.get("contactId"),
                                  Contact.workspace_id == ctx.workspace.id)
        )).scalar_one_or_none()
        if not contact:
            return {"isError": True, "content": [{"type": "text", "text": "Contact not found."}]}
        out = await reveal_contact(db, ctx, contact)
        return _text(out)

    if name == "enrich_linkedin":
        url = args.get("linkedinUrl", "")
        parsed = enrichment.parse_linkedin_url(url)
        if not parsed.get("is_valid"):
            return {"isError": True, "content": [{"type": "text", "text": "Not a valid LinkedIn profile URL."}]}
        parts = (parsed.get("name_guess") or "").split(" ", 1)
        res = await enrichment.enrich_person(
            parts[0] if parts else "",
            parts[1] if len(parts) > 1 else "",
            company_name=args.get("company"),
            domain=None,
            linkedin_url=url,
            providers={"hunter": settings.HUNTER_API_KEY, "apollo": settings.APOLLO_API_KEY,
                       "pdl": settings.PDL_API_KEY},
        )
        return _text(res)

    if name == "verify_email":
        return _text(await enrichment.verify_email(args.get("email", "")))

    if name == "list_folders":
        folders = (await db.execute(
            select(Folder).where(Folder.workspace_id == ctx.workspace.id)
        )).scalars().all()
        out = []
        for f in folders:
            count = (await db.execute(
                select(func.count()).select_from(FolderContact).where(FolderContact.folder_id == f.id)
            )).scalar() or 0
            out.append({"id": f.id, "name": f.name, "count": count})
        return _text(out)

    if name == "check_credits":
        return _text({"plan": ctx.workspace.plan, "credits": ctx.workspace.credits,
                      "verifierCredits": ctx.workspace.verifier_credits,
                      "recipientsUsed": ctx.workspace.recipients_used,
                      "recipientsLimit": ctx.workspace.recipients_limit})

    return {"isError": True, "content": [{"type": "text", "text": f"Unknown tool {name}"}]}


@router.post("/mcp")
async def mcp(request: Request, ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON-RPC body")

    method = body.get("method")
    req_id = body.get("id")
    params = body.get("params") or {}

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "lead-engine-mcp", "version": "0.1.0"},
        }}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        name = params.get("name")
        if name not in {t["name"] for t in TOOLS}:
            return {"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32601, "message": f"Unknown tool {name}"}}
        result = await _dispatch(name, params.get("arguments") or {}, ctx, db)
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": -32600, "message": "Unsupported JSON-RPC method"}}

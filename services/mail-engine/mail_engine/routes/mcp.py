"""Small MCP-shaped bridge so agents can drive the engine without the UI."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Ctx, get_ctx
from ..database import get_db
from ..models import Campaign, Lead
from ..serializers import campaign_dict, lead_dict

router = APIRouter(prefix="/mcp", tags=["mcp"])


class CallBody(BaseModel):
    tool: str
    arguments: dict | None = None


TOOLS = [
    {"name": "list_campaigns", "description": "List campaigns with stats",
     "input": {"status": "optional string"}},
    {"name": "get_campaign", "description": "Fetch one campaign and its steps",
     "input": {"campaignId": "string"}},
    {"name": "create_campaign", "description": "Create a draft campaign",
     "input": {"name": "string", "subject": "string", "body": "string"}},
    {"name": "list_leads", "description": "List leads, optionally filtered",
     "input": {"status": "optional string", "q": "optional search string"}},
    {"name": "add_lead", "description": "Add a single lead",
     "input": {"email": "string", "firstName": "string", "company": "string"}},
    {"name": "enroll_leads", "description": "Enroll every eligible lead in a campaign",
     "input": {"campaignId": "string", "all": "boolean"}},
    {"name": "send_next", "description": "Send the next batch of due steps (simulated)",
     "input": {"campaignId": "string", "limit": "integer"}},
    {"name": "analytics_overview", "description": "Workspace analytics totals", "input": {}},
]


@router.get("/tools")
async def list_tools():
    return {"tools": TOOLS}


@router.post("/call")
async def call_tool(body: CallBody, ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    args = body.arguments or {}
    tool = body.tool
    ws = ctx.workspace

    if tool == "list_campaigns":
        from .campaigns import campaign_stats, list_campaigns
        return {"ok": True, "result": await list_campaigns(args.get("status"), ctx, db)}

    if tool == "get_campaign":
        from .campaigns import get_campaign
        return {"ok": True, "result": await get_campaign(args["campaignId"], ctx, db)}

    if tool == "create_campaign":
        from .campaigns import create_campaign, CampaignBody
        return {"ok": True, "result": await create_campaign(CampaignBody(
            name=args.get("name", "Agent campaign"),
            subject=args.get("subject", ""),
            body=args.get("body", ""),
        ), ctx, db)}

    if tool == "list_leads":
        from .leads import list_leads
        return {"ok": True, "result": await list_leads(args.get("status"), args.get("q"), 100, ctx, db)}

    if tool == "add_lead":
        from .leads import create_lead, LeadBody
        return {"ok": True, "result": await create_lead(LeadBody(
            email=args.get("email", ""),
            firstName=args.get("firstName"),
            lastName=args.get("lastName"),
            company=args.get("company"),
            title=args.get("title"),
        ), ctx, db)}

    if tool == "enroll_leads":
        from .sending import enroll, EnrollBody
        return {"ok": True, "result": await enroll(
            args["campaignId"], EnrollBody(all=bool(args.get("all", True))), ctx, db)}

    if tool == "send_next":
        from .sending import send_next, SendNextBody
        return {"ok": True, "result": await send_next(
            args["campaignId"], SendNextBody(limit=args.get("limit", 10)), ctx, db)}

    if tool == "analytics_overview":
        from .analytics import overview
        return {"ok": True, "result": await overview(ctx, db)}

    raise HTTPException(status_code=404, detail=f"Unknown tool: {tool}")

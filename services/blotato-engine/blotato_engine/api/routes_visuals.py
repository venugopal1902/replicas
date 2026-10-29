import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..models import ApiKeyModel, VisualModel
from ..auth import get_current_user
from .. import visuals_render as vr

router = APIRouter(prefix="/v2/videos", tags=["visuals"])

GENERATED_DIR = Path(settings.MEDIA_DIR) / "generated"

TEMPLATES: List[Dict[str, Any]] = [
    {
        "id": "quote-card",
        "title": "Quote card",
        "description": "Square quote card with an accent rail. Good for X, LinkedIn and Instagram feed.",
        "inputs": [
            {"name": "text", "type": "string", "required": True, "description": "The quote body"},
            {"name": "author", "type": "string", "required": False},
            {"name": "palette", "type": "enum", "required": False,
             "options": ["midnight", "paper", "sunset"], "default": "midnight"},
        ],
    },
    {
        "id": "tweet-card",
        "title": "Tweet card",
        "description": "Screenshot-style post card for repurposing a text post as an image.",
        "inputs": [
            {"name": "text", "type": "string", "required": True},
            {"name": "name", "type": "string", "required": False, "default": "Venu"},
            {"name": "handle", "type": "string", "required": False, "default": "@venugopal1902"},
            {"name": "palette", "type": "enum", "required": False,
             "options": ["midnight", "paper"], "default": "midnight"},
        ],
    },
    {
        "id": "carousel",
        "title": "Instagram carousel",
        "description": "4:5 multi-slide carousel. Pass slides as an array of title/body pairs.",
        "inputs": [
            {"name": "slides", "type": "array", "required": True,
             "description": "[{title, body}] up to 10 slides"},
            {"name": "palette", "type": "enum", "required": False,
             "options": ["paper", "midnight", "sunset"], "default": "paper"},
        ],
    },
    {
        "id": "infographic-whiteboard",
        "title": "Whiteboard infographic",
        "description": "Numbered point list on a clean whiteboard. Reads well as a single image post.",
        "inputs": [
            {"name": "title", "type": "string", "required": True},
            {"name": "points", "type": "array", "required": True,
             "description": "Up to 6 short points"},
            {"name": "palette", "type": "enum", "required": False,
             "options": ["whiteboard", "paper"], "default": "whiteboard"},
        ],
    },
    {
        "id": "infographic-chalkboard",
        "title": "Chalkboard infographic",
        "description": "Same layout as the whiteboard template on a dark chalk palette.",
        "inputs": [
            {"name": "title", "type": "string", "required": True},
            {"name": "points", "type": "array", "required": True},
            {"name": "palette", "type": "enum", "required": False,
             "options": ["chalk"], "default": "chalk"},
        ],
    },
    {
        "id": "tutorial-carousel",
        "title": "Tutorial carousel",
        "description": "Step-by-step carousel. Each step becomes one slide with its number.",
        "inputs": [
            {"name": "steps", "type": "array", "required": True,
             "description": "[{title, body}] in order"},
            {"name": "palette", "type": "enum", "required": False,
             "options": ["paper", "midnight"], "default": "paper"},
        ],
    },
]


def _public_url(request: Request, filename: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/media/generated/{filename}"


def _visual_dict(v: VisualModel) -> Dict[str, Any]:
    return {
        "id": str(v.id),
        "templateId": v.template_id,
        "templateTitle": v.template_title,
        "status": v.status,
        "inputs": json.loads(v.inputs_json or "{}"),
        "outputs": json.loads(v.output_urls_json or "[]"),
        "error": v.error,
        "createdAt": v.created_at.isoformat() if v.created_at else None,
        "completedAt": v.completed_at.isoformat() if v.completed_at else None,
    }


def _render(template_id: str, inputs: Dict[str, Any], job_id: str, request: Request) -> List[str]:
    palette = inputs.get("palette")
    files: List[str] = []

    if template_id == "quote-card":
        img = vr.render_quote_card(inputs.get("text", ""), inputs.get("author", ""), palette or "midnight")
        files.append(vr.save(img, GENERATED_DIR, f"{job_id}-quote.png"))

    elif template_id == "tweet-card":
        img = vr.render_tweet_card(
            inputs.get("text", ""),
            inputs.get("name", "Venu"),
            inputs.get("handle", "@venugopal1902"),
            palette or "midnight",
        )
        files.append(vr.save(img, GENERATED_DIR, f"{job_id}-tweet.png"))

    elif template_id in ("carousel", "tutorial-carousel"):
        slides = inputs.get("slides") or inputs.get("steps") or []
        if not slides:
            raise ValueError("slides/steps array is required")
        total = min(len(slides), 10)
        for i in range(total):
            s = slides[i] if isinstance(slides[i], dict) else {"title": str(slides[i]), "body": ""}
            img = vr.render_carousel_slide(
                s.get("title", f"Slide {i + 1}"),
                s.get("body", ""),
                i + 1,
                total,
                palette or "paper",
            )
            files.append(vr.save(img, GENERATED_DIR, f"{job_id}-slide-{i + 1}.png"))

    elif template_id in ("infographic-whiteboard", "infographic-chalkboard"):
        points = inputs.get("points") or []
        if not points:
            raise ValueError("points array is required")
        default_pal = "chalk" if "chalk" in template_id else "whiteboard"
        img = vr.render_infographic(inputs.get("title", ""), points, palette or default_pal)
        files.append(vr.save(img, GENERATED_DIR, f"{job_id}-infographic.png"))

    else:
        raise ValueError(f"Unknown template: {template_id}")

    return [_public_url(request, f) for f in files]


@router.get("/templates")
async def list_templates(
    search: Optional[str] = None,
    id: Optional[str] = None,
):
    items = TEMPLATES
    if id:
        items = [t for t in TEMPLATES if t["id"] == id]
    elif search:
        needle = search.lower()
        items = [
            t for t in TEMPLATES
            if needle in t["title"].lower() or needle in t["description"].lower()
        ]
    return {"items": items}


@router.post("/from-templates")
async def create_from_template(
    payload: Dict[str, Any],
    request: Request,
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    template_id = payload.get("templateId") or payload.get("template")
    inputs = payload.get("inputs") or {}

    if not template_id:
        raise HTTPException(status_code=400, detail="templateId is required")
    template = next((t for t in TEMPLATES if t["id"] == template_id), None)
    if not template:
        raise HTTPException(status_code=404, detail=f"Unknown template '{template_id}'")

    visual = VisualModel(
        user_id=user.id,
        template_id=template_id,
        template_title=template["title"],
        status="rendering",
        inputs_json=json.dumps(inputs),
    )
    db.add(visual)
    await db.commit()
    await db.refresh(visual)

    try:
        urls = _render(template_id, inputs, str(visual.id), request)
        visual.status = "ready"
        visual.output_urls_json = json.dumps(urls)
        visual.completed_at = datetime.utcnow()
    except Exception as exc:  # noqa: BLE001
        visual.status = "failed"
        visual.error = str(exc)

    await db.commit()
    await db.refresh(visual)
    return _visual_dict(visual)


@router.get("/creations/{visual_id}")
async def get_creation(
    visual_id: str,
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(VisualModel).where(VisualModel.id == visual_id, VisualModel.user_id == user.id)
    )
    visual = res.scalar_one_or_none()
    if not visual:
        raise HTTPException(status_code=404, detail="Visual not found")
    return _visual_dict(visual)


@router.get("/creations")
async def list_creations(
    limit: int = Query(50, ge=1, le=250),
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(VisualModel)
        .where(VisualModel.user_id == user.id)
        .order_by(desc(VisualModel.created_at))
        .limit(limit)
    )
    return {"items": [_visual_dict(v) for v in res.scalars().all()]}


@router.delete("/{visual_id}")
async def delete_visual(
    visual_id: str,
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(VisualModel).where(VisualModel.id == visual_id, VisualModel.user_id == user.id)
    )
    visual = res.scalar_one_or_none()
    if not visual:
        raise HTTPException(status_code=404, detail="Visual not found")
    await db.delete(visual)
    await db.commit()
    return {"deleted": True}

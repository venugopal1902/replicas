import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import ApiKeyModel, SourceModel
from ..auth import get_current_user

router = APIRouter(prefix="/v2", tags=["sources"])

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def _strip_html(html: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = TAG_RE.sub(" ", text)
    return WS_RE.sub(" ", text).strip()


def _guess_type(url: str) -> str:
    u = (url or "").lower()
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "tiktok.com" in u:
        return "tiktok"
    if "twitter.com" in u or "x.com" in u:
        return "x"
    if u.endswith(".pdf"):
        return "pdf"
    if u.endswith((".mp3", ".wav", ".m4a", ".ogg")):
        return "audio"
    return "article"


async def _extract(source_type: str, url: Optional[str], text: Optional[str]) -> Dict[str, Any]:
    if source_type == "text":
        body = (text or "").strip()
        if not body:
            raise ValueError("text is required for sourceType=text")
        return {"title": body.split("\n")[0][:120], "body": body, "chars": len(body)}

    if not url:
        raise ValueError("url is required")

    async with httpx.AsyncClient(
        timeout=20.0, follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; BlotatoEngine/2.0)"},
    ) as client:
        if source_type == "youtube":
            try:
                oembed = await client.get(
                    "https://www.youtube.com/oembed", params={"url": url, "format": "json"}
                )
                meta = oembed.json() if oembed.status_code == 200 else {}
            except Exception:
                meta = {}
            page = await client.get(url)
            html = page.text if page.status_code == 200 else ""
            desc_match = re.search(r'"shortDescription":"(.*?)","', html, re.S)
            description = desc_match.group(1) if desc_match else ""
            return {
                "title": meta.get("title") or "YouTube video",
                "author": meta.get("author_name"),
                "thumbnail": meta.get("thumbnail_url"),
                "body": description.replace("\\n", " ")[:4000],
                "chars": len(description),
            }

        if source_type == "pdf":
            resp = await client.get(url)
            if resp.status_code != 200:
                raise ValueError(f"Could not download PDF ({resp.status_code})")
            try:
                import io

                from pypdf import PdfReader

                reader = PdfReader(io.BytesIO(resp.content))
                pages = [p.extract_text() or "" for p in reader.pages[:20]]
                body = WS_RE.sub(" ", " ".join(pages)).strip()
                return {
                    "title": url.rsplit("/", 1)[-1],
                    "pages": len(reader.pages),
                    "body": body[:8000],
                    "chars": len(body),
                }
            except ImportError:
                return {
                    "title": url.rsplit("/", 1)[-1],
                    "body": "",
                    "chars": 0,
                    "note": "Install pypdf to extract PDF text",
                }

        # article / x / tiktok / audio fallback: pull the page and strip it
        resp = await client.get(url)
        if resp.status_code != 200:
            raise ValueError(f"Could not fetch URL ({resp.status_code})")
        html = resp.text
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        og_match = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']', html, re.S | re.I)
        body = _strip_html(html)
        return {
            "title": (title_match.group(1).strip() if title_match else url)[:200],
            "description": og_match.group(1)[:500] if og_match else "",
            "body": body[:8000],
            "chars": len(body),
        }


def _repurpose(source: SourceModel, platforms: List[str]) -> List[Dict[str, Any]]:
    data = json.loads(source.extracted_json or "{}")
    body = data.get("body", "")
    title = data.get("title", "")

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if len(s.strip()) > 30]
    hook = sentences[0] if sentences else title
    points = sentences[1:6]

    drafts = []
    for p in platforms:
        if p in ("twitter", "x", "bluesky", "threads"):
            text = hook[:240]
        elif p == "linkedin":
            text = f"{title}\n\n" + "\n\n".join(f"\u2022 {pt}" for pt in points[:4]) + \
                   "\n\nWhat would you add?"
        elif p == "instagram":
            text = f"{hook}\n\n" + "\n".join(f"\u2192 {pt}" for pt in points[:4]) + \
                   "\n\nSave this for later."
        elif p == "youtube":
            text = f"{title}\n\n" + "\n".join(points[:5])
        else:
            text = hook[:300]
        drafts.append({
            "platform": p,
            "text": text.strip()[:2200],
            "sourceTitle": title,
        })
    return drafts


@router.post("/source-resolutions")
async def create_source(
    payload: Dict[str, Any],
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    url = payload.get("url")
    inline_text = payload.get("text")
    source_type = payload.get("sourceType") or payload.get("type")
    if not source_type:
        source_type = "text" if inline_text and not url else _guess_type(url or "")

    source = SourceModel(
        user_id=user.id,
        source_type=source_type,
        source_url=url,
        status="processing",
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)

    try:
        extracted = await _extract(source_type, url, inline_text)
        source.extracted_json = json.dumps(extracted)
        source.status = "ready"
    except Exception as exc:  # noqa: BLE001
        source.status = "failed"
        source.error = str(exc)

    await db.commit()
    await db.refresh(source)

    return {
        "id": str(source.id),
        "sourceType": source.source_type,
        "status": source.status,
        "error": source.error,
        "extracted": json.loads(source.extracted_json or "{}"),
    }


@router.get("/source-resolutions/{source_id}")
async def get_source(
    source_id: str,
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(SourceModel).where(SourceModel.id == source_id, SourceModel.user_id == user.id)
    )
    source = res.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return {
        "id": str(source.id),
        "sourceType": source.source_type,
        "url": source.source_url,
        "status": source.status,
        "error": source.error,
        "extracted": json.loads(source.extracted_json or "{}"),
    }


@router.get("/source-resolutions")
async def list_sources(
    limit: int = Query(50, ge=1, le=250),
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(SourceModel)
        .where(SourceModel.user_id == user.id)
        .order_by(desc(SourceModel.created_at))
        .limit(limit)
    )
    sources = res.scalars().all()
    return {
        "items": [
            {
                "id": str(s.id),
                "sourceType": s.source_type,
                "url": s.source_url,
                "status": s.status,
                "title": json.loads(s.extracted_json or "{}").get("title"),
                "createdAt": s.created_at.isoformat() if s.created_at else None,
            }
            for s in sources
        ]
    }


@router.post("/source-resolutions/{source_id}/repurpose")
async def repurpose_source(
    source_id: str,
    payload: Dict[str, Any],
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(SourceModel).where(SourceModel.id == source_id, SourceModel.user_id == user.id)
    )
    source = res.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    if source.status != "ready":
        raise HTTPException(status_code=409, detail=f"Source is {source.status}, not ready")

    platforms = payload.get("platforms") or ["instagram", "twitter", "linkedin"]
    return {"drafts": _repurpose(source, platforms)}

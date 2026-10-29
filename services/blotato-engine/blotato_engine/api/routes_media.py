import json
import uuid
from pathlib import Path
from typing import Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from ..database import get_db
from ..models import ApiKeyModel, MediaAssetModel
from ..auth import get_current_user
from ..config import settings

router = APIRouter(prefix="/v2", tags=["media"])

MAX_BYTES = 250 * 1024 * 1024  # 250 MB, matches Blotato's per-file ceiling


def _public_base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _safe_name(name: str) -> str:
    keep = "".join(c for c in name if c.isalnum() or c in "._-")
    return keep or "asset.bin"


@router.post("/media")
async def request_upload(
    payload: Dict[str, Any],
    request: Request,
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Two-step upload: reserve a media id, then PUT the bytes to the returned URL."""
    filename = _safe_name(payload.get("fileName") or payload.get("filename") or "asset.bin")
    content_type = payload.get("contentType") or "application/octet-stream"
    size = int(payload.get("size") or 0)

    if size > MAX_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 250 MB limit")

    media_id = str(uuid.uuid4())
    stored_name = f"{media_id}-{filename}"
    base = _public_base(request)
    public_url = f"{base}/media/{stored_name}"

    asset = MediaAssetModel(
        id=media_id,
        user_id=user.id,
        filename=filename,
        content_type=content_type,
        size_bytes=size,
        public_url=public_url,
        storage_path=str(Path(settings.MEDIA_DIR) / stored_name),
    )
    db.add(asset)
    await db.commit()

    return {
        "mediaId": media_id,
        "uploadUrl": f"{base}/v2/media/{media_id}/raw",
        "publicUrl": public_url,
        "method": "PUT",
        "contentType": content_type,
        "expiresInSeconds": 3600,
    }


@router.put("/media/{media_id}/raw")
async def upload_raw(
    media_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Raw byte sink for the reserved upload URL. No key required: URL is unguessable."""
    res = await db.execute(select(MediaAssetModel).where(MediaAssetModel.id == media_id))
    asset = res.scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="Unknown media id")

    body = await request.body()
    if len(body) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 250 MB limit")

    path = Path(asset.storage_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)

    asset.size_bytes = len(body)
    await db.commit()

    return {"mediaId": media_id, "size": len(body), "publicUrl": asset.public_url}


@router.post("/media/upload")
async def upload_multipart(
    request: Request,
    file: UploadFile = File(...),
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Single-call convenience upload (Blotato equivalent of POST /v2/media with bytes inline)."""
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 250 MB limit")

    media_id = str(uuid.uuid4())
    filename = _safe_name(file.filename or "asset.bin")
    stored_name = f"{media_id}-{filename}"
    path = Path(settings.MEDIA_DIR) / stored_name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)

    base = _public_base(request)
    asset = MediaAssetModel(
        id=media_id,
        user_id=user.id,
        filename=filename,
        content_type=file.content_type or "application/octet-stream",
        size_bytes=len(data),
        public_url=f"{base}/media/{stored_name}",
        storage_path=str(path),
    )
    db.add(asset)
    await db.commit()

    return {
        "mediaId": media_id,
        "publicUrl": asset.public_url,
        "size": len(data),
        "contentType": asset.content_type,
    }


@router.get("/media")
async def list_media(
    limit: int = 50,
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(MediaAssetModel)
        .where(MediaAssetModel.user_id == user.id)
        .order_by(desc(MediaAssetModel.created_at))
        .limit(limit)
    )
    items = res.scalars().all()
    return {
        "items": [
            {
                "mediaId": m.id,
                "filename": m.filename,
                "contentType": m.content_type,
                "size": m.size_bytes,
                "url": m.public_url,
                "createdAt": m.created_at.isoformat() if m.created_at else None,
            }
            for m in items
        ]
    }

from __future__ import annotations

import asyncio
import json
import secrets
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import (AdminContext, ProjectContext, create_admin_session, get_admin_ctx,
                   get_project_ctx, hash_password, token_hash, verify_password)
from .config import settings
from .database import AsyncSessionLocal, get_db
from .models import (AdminUser, Asset, Project, ProjectAPIKey, RenderJob, Template, Usage, utc_now)
from .rendering import inspect_image, render_png

router = APIRouter()


class LoginBody(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=256)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    credits: int = Field(ge=0, le=10_000_000)


class KeyCreate(BaseModel):
    label: str = Field(default="default", min_length=1, max_length=120)


class TemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    width: int = Field(ge=16, le=4096)
    height: int = Field(ge=16, le=4096)
    background: str = Field(default="#111827", pattern=r"^#[0-9a-fA-F]{6}$")
    layers: list[dict[str, Any]] = Field(min_length=1, max_length=40)


class RenderCreate(BaseModel):
    template_id: str = Field(min_length=1, max_length=36)
    overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)


def _json_template(row: Template) -> dict[str, Any]:
    return {"id": row.id, "name": row.name, "width": row.width, "height": row.height,
            "background": row.background, "layers": json.loads(row.layers_json),
            "created_at": row.created_at.isoformat() if row.created_at else None}


def _validate_layers(layers: list[dict[str, Any]], width: int, height: int) -> None:
    seen: set[str] = set()
    for layer in layers:
        name = layer.get("name")
        kind = layer.get("type")
        if not isinstance(name, str) or not name.strip() or len(name) > 80 or name in seen:
            raise HTTPException(status_code=422, detail="Layer names must be unique non-empty strings")
        seen.add(name)
        for field in ("x", "y"):
            value = layer.get(field)
            if not isinstance(value, (int, float)) or not -4096 <= value <= 8192:
                raise HTTPException(status_code=422, detail=f"Layer {field} must be a bounded number")
        if kind == "text":
            if not isinstance(layer.get("text", ""), str) or len(layer.get("text", "")) > 2000:
                raise HTTPException(status_code=422, detail="Text layer text must be at most 2000 characters")
            if not isinstance(layer.get("font_size", 48), int) or not 6 <= layer.get("font_size", 48) <= 512:
                raise HTTPException(status_code=422, detail="Text font_size must be between 6 and 512")
            if not isinstance(layer.get("color", "#ffffff"), str) or len(layer.get("color", "#ffffff")) != 7 or not layer.get("color", "#ffffff").startswith("#"):
                raise HTTPException(status_code=422, detail="Text color must use #RRGGBB format")
        elif kind == "image":
            if not isinstance(layer.get("asset_id"), str) or not layer.get("asset_id"):
                raise HTTPException(status_code=422, detail="Image layer requires an uploaded asset_id")
            for field in ("w", "h"):
                value = layer.get(field)
                if not isinstance(value, int) or value < 1 or value > 4096:
                    raise HTTPException(status_code=422, detail=f"Image {field} must be between 1 and 4096")
        else:
            raise HTTPException(status_code=422, detail="Layer type must be text or image")


@router.post("/api/auth/login")
async def login(body: LoginBody, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(AdminUser).where(
        AdminUser.email == body.email.lower().strip()
    ))).scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")
    token = await create_admin_session(user, db)
    return {"access_token": token, "token_type": "bearer", "expires_in": 43200,
            "user": {"id": user.id, "email": user.email, "name": user.name}}


@router.post("/api/admin/projects", status_code=status.HTTP_201_CREATED)
async def create_project(body: ProjectCreate, _: AdminContext = Depends(get_admin_ctx),
                         db: AsyncSession = Depends(get_db)):
    project = Project(name=body.name.strip(), credits=body.credits)
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return {"id": project.id, "name": project.name, "credits": project.credits}


@router.get("/api/admin/projects")
async def list_projects(_: AdminContext = Depends(get_admin_ctx), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Project).order_by(Project.created_at.desc()))).scalars().all()
    return {"items": [{"id": row.id, "name": row.name, "credits": row.credits} for row in rows]}


@router.post("/api/admin/projects/{project_id}/keys", status_code=status.HTTP_201_CREATED)
async def create_project_key(project_id: str, body: KeyCreate, _: AdminContext = Depends(get_admin_ctx),
                             db: AsyncSession = Depends(get_db)):
    project = (await db.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    secret = "bb_live_" + secrets.token_urlsafe(32)
    key = ProjectAPIKey(project_id=project.id, key_hash=token_hash(secret),
                        prefix=secret[:15], label=body.label.strip())
    db.add(key)
    await db.commit()
    return {"id": key.id, "project_id": project.id, "label": key.label,
            "api_key": secret, "shown_once": True}


@router.get("/api/admin/usage")
async def admin_usage(_: AdminContext = Depends(get_admin_ctx), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Usage).order_by(Usage.created_at.desc()).limit(500))).scalars().all()
    return {"items": [{"id": row.id, "project_id": row.project_id, "job_id": row.job_id,
                        "credits": row.credits, "balance_after": row.balance_after,
                        "description": row.description, "created_at": row.created_at.isoformat()}
                       for row in rows]}


@router.get("/api/projects/me")
async def project_me(ctx: ProjectContext = Depends(get_project_ctx)):
    return {"project_id": ctx.project.id, "name": ctx.project.name, "credits": ctx.project.credits}


@router.get("/api/projects/me/usage")
async def project_usage(ctx: ProjectContext = Depends(get_project_ctx), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Usage).where(Usage.project_id == ctx.project.id)
                             .order_by(Usage.created_at.desc()).limit(500))).scalars().all()
    return {"items": [{"job_id": row.job_id, "credits": row.credits,
                        "balance_after": row.balance_after, "description": row.description,
                        "created_at": row.created_at.isoformat()} for row in rows]}


@router.post("/api/assets", status_code=status.HTTP_201_CREATED)
async def upload_asset(file: UploadFile = File(...), ctx: ProjectContext = Depends(get_project_ctx),
                       db: AsyncSession = Depends(get_db)):
    data = await file.read(5 * 1024 * 1024 + 1)
    try:
        mime, width, height = inspect_image(data)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    asset_id = str(uuid.uuid4())
    ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[mime]
    filename = f"{asset_id}{ext}"
    destination = settings.DATA_DIR / "assets" / filename
    destination.write_bytes(data)
    asset = Asset(id=asset_id, project_id=ctx.project.id, filename=filename,
                  mime_type=mime, size_bytes=len(data))
    db.add(asset)
    await db.commit()
    return {"asset_id": asset.id, "mime_type": mime, "width": width, "height": height,
            "size_bytes": len(data)}


@router.post("/api/templates", status_code=status.HTTP_201_CREATED)
async def create_template(body: TemplateCreate, ctx: ProjectContext = Depends(get_project_ctx),
                          db: AsyncSession = Depends(get_db)):
    _validate_layers(body.layers, body.width, body.height)
    asset_ids = [layer["asset_id"] for layer in body.layers if layer.get("type") == "image"]
    if asset_ids:
        assets = (await db.execute(select(Asset).where(
            Asset.project_id == ctx.project.id, Asset.id.in_(asset_ids)
        ))).scalars().all()
        if len({asset.id for asset in assets}) != len(set(asset_ids)):
            raise HTTPException(status_code=422, detail="Image layers must reference assets in this project")
    row = Template(project_id=ctx.project.id, name=body.name.strip(), width=body.width,
                   height=body.height, background=body.background,
                   layers_json=json.dumps(body.layers, ensure_ascii=False))
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _json_template(row)


@router.get("/api/templates")
async def list_templates(ctx: ProjectContext = Depends(get_project_ctx), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Template).where(Template.project_id == ctx.project.id)
                             .order_by(Template.created_at.desc()))).scalars().all()
    return {"items": [_json_template(row) for row in rows]}


@router.get("/api/templates/{template_id}")
async def get_template(template_id: str, ctx: ProjectContext = Depends(get_project_ctx),
                       db: AsyncSession = Depends(get_db)):
    row = (await db.execute(select(Template).where(
        Template.id == template_id, Template.project_id == ctx.project.id
    ))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return _json_template(row)


def _validate_overrides(template_layers: list[dict[str, Any]], overrides: dict[str, dict[str, Any]]) -> None:
    by_name = {layer["name"]: layer for layer in template_layers}
    if set(overrides) - set(by_name):
        raise HTTPException(status_code=422, detail="Override references an unknown layer")
    allowed = {"text": {"text", "font_size", "color", "x", "y"},
               "image": {"asset_id", "x", "y", "w", "h"}}
    for name, values in overrides.items():
        layer = by_name[name]
        if set(values) - allowed[layer["type"]]:
            raise HTTPException(status_code=422, detail=f"Unsupported override for layer {name}")
        merged = {**layer, **values}
        if layer["type"] == "text":
            if not isinstance(merged.get("text", ""), str) or len(merged.get("text", "")) > 2000:
                raise HTTPException(status_code=422, detail="Text override must be at most 2000 characters")
            if not isinstance(merged.get("font_size", 48), int) or not 6 <= merged.get("font_size", 48) <= 512:
                raise HTTPException(status_code=422, detail="font_size must be between 6 and 512")
            if not isinstance(merged.get("color", "#ffffff"), str) or len(merged.get("color", "#ffffff")) != 7:
                raise HTTPException(status_code=422, detail="color must use #RRGGBB format")
        else:
            if not isinstance(merged.get("asset_id"), str):
                raise HTTPException(status_code=422, detail="Image override requires asset_id")
            for key in ("x", "y", "w", "h"):
                if not isinstance(merged.get(key), int) or not -4096 <= merged[key] <= 4096:
                    raise HTTPException(status_code=422, detail=f"Invalid image override {key}")


async def _render_job(job_id: str) -> None:
    async with AsyncSessionLocal() as db:
        job = (await db.execute(select(RenderJob).where(RenderJob.id == job_id))).scalar_one_or_none()
        if job is None:
            return
        job.status = "rendering"
        await db.commit()
        project_id = job.project_id
        try:
            template = (await db.execute(select(Template).where(
                Template.id == job.template_id, Template.project_id == project_id
            ))).scalar_one()
            layers = json.loads(template.layers_json)
            overrides = json.loads(job.overrides_json)
            referenced = {layer["asset_id"] for layer in layers if layer.get("type") == "image"}
            referenced.update(values["asset_id"] for values in overrides.values()
                              if isinstance(values, dict) and values.get("asset_id"))
            assets = (await db.execute(select(Asset).where(
                Asset.project_id == project_id, Asset.id.in_(referenced)
            ))).scalars().all() if referenced else []
            asset_paths = {asset.id: settings.DATA_DIR / "assets" / asset.filename for asset in assets}
            output = settings.DATA_DIR / "renders" / f"render-{job.id}.png"
            template_data = {"width": template.width, "height": template.height,
                             "background": template.background, "layers": layers}
            await asyncio.to_thread(render_png, template_data, overrides, asset_paths, output)
            job.output_filename = output.name
            job.status = "done"
            job.completed_at = utc_now()
            job.error = None
            await db.commit()
        except Exception:
            await db.rollback()
            job = (await db.execute(select(RenderJob).where(RenderJob.id == job_id))).scalar_one()
            project = (await db.execute(select(Project).where(Project.id == project_id))).scalar_one()
            job.status = "failed"
            job.error = "Render failed; credits refunded. Check template and image assets."
            job.completed_at = utc_now()
            project.credits += job.credit_cost
            db.add(Usage(project_id=project.id, job_id=job.id, credits=-job.credit_cost,
                         balance_after=project.credits, description="Render failure refund"))
            await db.commit()


def _job_dict(job: RenderJob) -> dict[str, Any]:
    return {"job_id": job.id, "template_id": job.template_id, "status": job.status,
            "credit_cost": job.credit_cost, "error": job.error,
            "download_url": f"/api/renders/{job.id}/download" if job.status == "done" else None,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None}


@router.post("/api/renders", status_code=status.HTTP_202_ACCEPTED)
async def create_render(body: RenderCreate, background: BackgroundTasks,
                        ctx: ProjectContext = Depends(get_project_ctx), db: AsyncSession = Depends(get_db)):
    template = (await db.execute(select(Template).where(
        Template.id == body.template_id, Template.project_id == ctx.project.id
    ))).scalar_one_or_none()
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    layers = json.loads(template.layers_json)
    _validate_overrides(layers, body.overrides)
    assets_needed = {layer["asset_id"] for layer in layers if layer.get("type") == "image"}
    assets_needed.update(values["asset_id"] for values in body.overrides.values()
                         if isinstance(values, dict) and values.get("asset_id"))
    if assets_needed:
        rows = (await db.execute(select(Asset).where(
            Asset.project_id == ctx.project.id, Asset.id.in_(assets_needed)
        ))).scalars().all()
        if len({row.id for row in rows}) != len(assets_needed):
            raise HTTPException(status_code=422, detail="Image layer references an unavailable asset")
    cost = settings.RENDER_CREDITS
    debit = await db.execute(update(Project).where(
        Project.id == ctx.project.id, Project.credits >= cost
    ).values(credits=Project.credits - cost))
    if debit.rowcount != 1:
        await db.rollback()
        raise HTTPException(status_code=402, detail="Insufficient project credits")
    await db.refresh(ctx.project)
    job = RenderJob(project_id=ctx.project.id, template_id=template.id, status="queued",
                    overrides_json=json.dumps(body.overrides, ensure_ascii=False), credit_cost=cost)
    db.add(job)
    await db.flush()
    db.add(Usage(project_id=ctx.project.id, job_id=job.id, credits=cost,
                 balance_after=ctx.project.credits, description="PNG render"))
    await db.commit()
    await db.refresh(job)
    background.add_task(_render_job, job.id)
    return _job_dict(job)


@router.get("/api/renders/{job_id}")
async def get_render(job_id: str, ctx: ProjectContext = Depends(get_project_ctx), db: AsyncSession = Depends(get_db)):
    job = (await db.execute(select(RenderJob).where(
        RenderJob.id == job_id, RenderJob.project_id == ctx.project.id
    ))).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Render job not found")
    return _job_dict(job)


@router.get("/api/renders/{job_id}/download")
async def download_render(job_id: str, ctx: ProjectContext = Depends(get_project_ctx),
                          db: AsyncSession = Depends(get_db)):
    job = (await db.execute(select(RenderJob).where(
        RenderJob.id == job_id, RenderJob.project_id == ctx.project.id
    ))).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Render job not found")
    if job.status != "done" or not job.output_filename:
        raise HTTPException(status_code=409, detail="Render is not ready")
    path = settings.DATA_DIR / "renders" / job.output_filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Rendered file not found")
    return FileResponse(path, media_type="image/png", filename=job.output_filename)


@router.get("/api/admin/health")
async def admin_health(_: AdminContext = Depends(get_admin_ctx)):
    return {"status": "ok"}

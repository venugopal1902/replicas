"""Workspace-scoped data-provider keys (Hunter, Apollo, People Data Labs).

Keys are stored raw on the workspace row and only ever returned masked (last 4
characters). An empty string or null clears a provider. Connectivity checks are
one cheap authenticated request per configured provider and never raise.
"""
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Ctx, get_ctx
from ..database import get_db
from ..models import Workspace

router = APIRouter(prefix="/api", tags=["providers"])

_PROVIDERS = ("hunter", "apollo", "pdl")
_TIMEOUT = 12.0


class ProvidersBody(BaseModel):
    hunter: Optional[str] = None
    apollo: Optional[str] = None
    pdl: Optional[str] = None


def _raw(workspace: Workspace, name: str) -> Optional[str]:
    value = getattr(workspace, f"{name}_key", None)
    return value or None


def mask_key(value: Optional[str]) -> Optional[str]:
    """Last 4 characters, bullet-masked. ``None`` when nothing is stored."""
    if not value:
        return None
    tail = value[-4:] if len(value) >= 4 else value
    return "••••" + tail


def provider_items(workspace: Workspace) -> List[Dict[str, Any]]:
    items = []
    for name in _PROVIDERS:
        raw = _raw(workspace, name)
        items.append({"name": name, "configured": bool(raw), "masked": mask_key(raw)})
    return items


def _refresh_status(workspace: Workspace) -> None:
    workspace.provider_status = (
        "configured" if any(_raw(workspace, name) for name in _PROVIDERS) else "unconfigured"
    )


@router.get("/providers")
async def list_providers(ctx: Ctx = Depends(get_ctx)):
    items = provider_items(ctx.workspace)
    return {"items": items, "anyConfigured": any(i["configured"] for i in items)}


@router.put("/providers")
async def update_providers(body: ProvidersBody, ctx: Ctx = Depends(get_ctx),
                           db: AsyncSession = Depends(get_db)):
    workspace = ctx.workspace
    data = body.model_dump(exclude_unset=True)
    for name in _PROVIDERS:
        if name not in data:
            continue
        value = data[name]
        setattr(workspace, f"{name}_key", (value.strip() or None) if isinstance(value, str) else None)
    _refresh_status(workspace)
    await db.commit()
    await db.refresh(workspace)
    items = provider_items(workspace)
    return {"items": items, "anyConfigured": any(i["configured"] for i in items)}


async def _probe(client: httpx.AsyncClient, name: str, key: str) -> Dict[str, Any]:
    """One cheap authenticated call. Any non-2xx (or transport error) is unreachable."""
    try:
        if name == "hunter":
            response = await client.get(
                "https://api.hunter.io/v2/account", params={"api_key": key}
            )
        elif name == "apollo":
            response = await client.post(
                "https://api.apollo.io/v1/auth/health", headers={"x-api-key": key}
            )
        else:
            response = await client.get(
                "https://api.peopledatalabs.com/v5/company/enrich",
                params={"website": "stripe.com"},
                headers={"X-Api-Key": key},
            )
    except Exception as exc:  # noqa: BLE001 — a probe must never raise
        return {"provider": name, "reachable": False, "detail": f"request failed: {exc.__class__.__name__}"}

    if 200 <= response.status_code < 300:
        return {"provider": name, "reachable": True, "detail": f"HTTP {response.status_code}"}
    return {"provider": name, "reachable": False, "detail": f"HTTP {response.status_code}"}


@router.post("/providers/test")
async def test_providers(ctx: Ctx = Depends(get_ctx)):
    workspace = ctx.workspace
    results: List[Dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for name in _PROVIDERS:
                key = _raw(workspace, name)
                if not key:
                    continue
                results.append(await _probe(client, name, key))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "results": results or [
            {"provider": "all", "reachable": False, "detail": exc.__class__.__name__}
        ]}
    return {"ok": bool(results) and all(r["reachable"] for r in results), "results": results}

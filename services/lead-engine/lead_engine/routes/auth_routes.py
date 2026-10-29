from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import (
    Ctx, create_session, get_ctx, hash_password, verify_password,
)
from ..database import get_db
from ..models import User, Workspace, WorkspaceMember, Folder, ApiKey

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    email: str
    password: str


class SignupBody(BaseModel):
    email: str
    password: str
    name: str | None = None


def user_dict(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name}


def workspace_dict(ws: Workspace, seats_used: int = 1) -> dict:
    return {
        "id": ws.id,
        "name": ws.name,
        "plan": ws.plan,
        "credits": ws.credits,
        "verifierCredits": ws.verifier_credits,
        "seats": ws.seats,
        "seatsUsed": seats_used,
        "recipientsLimit": ws.recipients_limit,
        "recipientsUsed": ws.recipients_used,
        "apiCallsLimit": ws.api_calls_limit,
    }


async def _seats_used(workspace_id: str, db: AsyncSession) -> int:
    rows = (await db.execute(
        select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id)
    )).scalars().all()
    return max(len(rows), 1)


@router.post("/login")
async def login(body: LoginBody, response: Response, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.email == body.email.lower().strip()))).scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")

    member = (await db.execute(
        select(WorkspaceMember).where(WorkspaceMember.user_id == user.id)
    )).scalars().first()
    workspace = None
    if member:
        workspace = (await db.execute(
            select(Workspace).where(Workspace.id == member.workspace_id)
        )).scalar_one_or_none()
    if not workspace:
        workspace = (await db.execute(
            select(Workspace).where(Workspace.owner_id == user.id)
        )).scalars().first()
    if not workspace:
        raise HTTPException(status_code=500, detail="No workspace bound to this user")

    token = await create_session(user, workspace, db)
    response.set_cookie("lead_session", token, httponly=True, samesite="lax", max_age=86400 * 30)
    return {
        "token": token,
        "user": user_dict(user),
        "workspace": workspace_dict(workspace, await _seats_used(workspace.id, db)),
    }


@router.post("/signup")
async def signup(body: SignupBody, response: Response, db: AsyncSession = Depends(get_db)):
    email = body.email.lower().strip()
    if (await db.execute(select(User).where(User.email == email))).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="An account with that email already exists")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    user = User(email=email, password_hash=hash_password(body.password), name=body.name or email.split("@")[0])
    db.add(user)
    await db.commit()
    await db.refresh(user)

    ws = Workspace(name=f"{user.name}'s workspace", owner_id=user.id, plan="free",
                   credits=50, verifier_credits=100, seats=1,
                   recipients_limit=100, api_calls_limit=0)
    db.add(ws)
    await db.commit()
    await db.refresh(ws)

    db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
    db.add(Folder(workspace_id=ws.id, name="My default folder", created_by=user.id))
    db.add(ApiKey(workspace_id=ws.id, user_id=user.id, key=f"lke_live_{user.id.replace('-', '')[:32]}",
                  label="default", calls_limit=0))
    await db.commit()

    token = await create_session(user, ws, db)
    response.set_cookie("lead_session", token, httponly=True, samesite="lax", max_age=86400 * 30)
    return {"token": token, "user": user_dict(user), "workspace": workspace_dict(ws, 1)}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("lead_session")
    return {"ok": True}


@router.get("/me")
async def me(ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    return {
        "user": user_dict(ctx.user),
        "workspace": workspace_dict(ctx.workspace, await _seats_used(ctx.workspace.id, db)),
    }

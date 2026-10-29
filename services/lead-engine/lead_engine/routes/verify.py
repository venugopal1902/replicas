from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from .. import enrichment
from ..auth import Ctx, get_ctx
from ..common import spend_credits
from ..database import get_db
from ..models import EmailVerification

router = APIRouter(prefix="/api", tags=["verifier"])

MAX_PER_CALL = 100


class VerifyBody(BaseModel):
    emails: List[str]


@router.post("/verify")
async def verify_emails(body: VerifyBody, ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    emails = [e.strip() for e in body.emails if e and e.strip()]
    if not emails:
        raise HTTPException(status_code=400, detail="Provide at least one email")
    if len(emails) > MAX_PER_CALL:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_PER_CALL} emails per call")

    items = []
    spent = 0

    for email in emails:
        try:
            res = await enrichment.verify_email(email)
        except Exception as exc:  # noqa: BLE001
            res = {"email": email, "result": "unknown", "score": "C",
                   "mxHost": None, "reason": f"verification error: {exc}"}

        charged = 0
        if res.get("result") != "invalid" or True:  # verifier credits are per check
            try:
                await spend_credits(db, ctx.workspace, 1, "email verification", pool="verifier",
                                    user_id=ctx.user.id)
                charged = 1
                spent += 1
            except ValueError:
                items.append({**res, "charged": 0, "note": "out of verifier credits"})
                continue

        db.add(EmailVerification(
            workspace_id=ctx.workspace.id,
            email=email,
            result=res.get("result"),
            score=res.get("score"),
            mx_host=res.get("mxHost"),
            reason=res.get("reason"),
            credits_spent=charged,
        ))
        items.append({**res, "charged": charged})

    await db.commit()

    return {
        "items": items,
        "creditsSpent": spent,
        "verifierBalance": ctx.workspace.verifier_credits,
    }

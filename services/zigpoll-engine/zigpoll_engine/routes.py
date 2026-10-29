from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import secrets
from typing import Any, Literal

import httpx
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Request,
    status,
)
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import AdminContext, create_admin_session, get_admin_ctx, verify_password
from .config import settings
from .database import AsyncSessionLocal, get_db
from .models import AdminUser, Survey, SurveyResponse, WebhookEvent, utc_now

router = APIRouter()


class LoginBody(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=256)


class Question(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    label: str = Field(min_length=1, max_length=300)
    type: Literal["single_choice", "multi_choice", "rating", "free_text"]
    required: bool = True
    options: list[str] = Field(default_factory=list, max_length=40)
    min_value: int = Field(default=1, ge=0, le=100)
    max_value: int = Field(default=5, ge=1, le=100)

    @model_validator(mode="after")
    def validate_type_fields(self):
        if self.type in ("single_choice", "multi_choice"):
            if len(self.options) < 2 or len(set(self.options)) != len(self.options):
                raise ValueError("Choice questions need at least two unique options")
            if any(not option.strip() or len(option) > 200 for option in self.options):
                raise ValueError("Choice options must be non-empty and at most 200 characters")
        elif self.options:
            raise ValueError("Only choice questions may define options")
        if self.type == "rating" and self.max_value <= self.min_value:
            raise ValueError("Rating max_value must be greater than min_value")
        return self


class SurveyCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    status: Literal["draft", "active", "closed"] = "active"
    webhook_url: str | None = Field(default=None, max_length=2048, pattern=r"^https://.+")
    questions: list[Question] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def unique_question_ids(self):
        ids = [question.id for question in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("Question ids must be unique within a survey")
        return self


class SurveyUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    status: Literal["draft", "active", "closed"] | None = None
    webhook_url: str | None = Field(default=None, max_length=2048, pattern=r"^https://.+")
    questions: list[Question] | None = Field(default=None, min_length=1, max_length=30)

    @model_validator(mode="after")
    def unique_question_ids(self):
        if self.questions is not None:
            ids = [question.id for question in self.questions]
            if len(ids) != len(set(ids)):
                raise ValueError("Question ids must be unique within a survey")
        return self


class PublicResponseCreate(BaseModel):
    answers: dict[str, Any]
    respondent_id: str | None = Field(default=None, max_length=120)


def _questions(survey: Survey) -> list[dict[str, Any]]:
    return json.loads(survey.questions_json)


def _survey_dict(survey: Survey, include_secret: bool = False) -> dict[str, Any]:
    result = {
        "id": survey.id,
        "public_id": survey.public_id,
        "title": survey.title,
        "description": survey.description,
        "status": survey.status,
        "webhook_url": survey.webhook_url,
        "questions": _questions(survey),
        "created_at": survey.created_at.isoformat(),
        "updated_at": survey.updated_at.isoformat(),
        "embed_url": f"/s/{survey.public_id}",
    }
    if include_secret:
        result["webhook_secret"] = survey.webhook_secret
        result["webhook_secret_shown_once"] = True
    return result


async def _get_survey(db: AsyncSession, survey_id: str) -> Survey:
    row = (await db.execute(select(Survey).where(Survey.id == survey_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Survey not found")
    return row


def _validate_answers(questions: list[dict[str, Any]], answers: dict[str, Any]) -> dict[str, Any]:
    by_id = {question["id"]: question for question in questions}
    unknown = set(answers) - set(by_id)
    if unknown:
        raise HTTPException(status_code=422, detail="Answers include an unknown question id")
    cleaned: dict[str, Any] = {}
    for question in questions:
        qid = question["id"]
        value = answers.get(qid)
        if value is None or value == "" or value == []:
            if question.get("required", True):
                raise HTTPException(status_code=422, detail=f"Answer required for question {qid}")
            continue
        kind = question["type"]
        if kind == "single_choice":
            if not isinstance(value, str) or value not in question["options"]:
                raise HTTPException(status_code=422, detail=f"Invalid choice for question {qid}")
        elif kind == "multi_choice":
            if (not isinstance(value, list) or not value
                    or any(not isinstance(item, str) or item not in question["options"] for item in value)
                    or len(value) != len(set(value))):
                raise HTTPException(status_code=422, detail=f"Invalid multiple choice for question {qid}")
        elif kind == "rating":
            if (isinstance(value, bool) or not isinstance(value, int)
                    or not question["min_value"] <= value <= question["max_value"]):
                raise HTTPException(status_code=422, detail=f"Rating out of range for question {qid}")
        elif kind == "free_text" and (not isinstance(value, str) or len(value) > 4000):
            raise HTTPException(status_code=422, detail=f"Text answer must be at most 4000 characters for {qid}")
        cleaned[qid] = value
    return cleaned


@router.get("/health")
async def health():
    return {"status": "online", "service": settings.APP_NAME, "version": settings.VERSION, "port": settings.PORT}


@router.post("/api/auth/login")
async def login(body: LoginBody, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(AdminUser).where(
        AdminUser.email == body.email.lower().strip()
    ))).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")
    token = await create_admin_session(user, db)
    return {"access_token": token, "token_type": "bearer", "expires_in": 3600 * 12,
            "user": {"id": user.id, "email": user.email, "name": user.name}}


@router.post("/api/admin/surveys", status_code=status.HTTP_201_CREATED)
async def create_survey(body: SurveyCreate, _: AdminContext = Depends(get_admin_ctx),
                        db: AsyncSession = Depends(get_db)):
    survey = Survey(
        public_id="zp_" + secrets.token_urlsafe(12),
        title=body.title.strip(), description=body.description,
        status=body.status,
        webhook_url=body.webhook_url,
        questions_json=json.dumps([question.model_dump() for question in body.questions], ensure_ascii=False),
        webhook_secret="zph_" + secrets.token_urlsafe(32),
    )
    db.add(survey)
    await db.commit()
    await db.refresh(survey)
    return _survey_dict(survey, include_secret=True)


@router.get("/api/admin/surveys")
async def list_surveys(_: AdminContext = Depends(get_admin_ctx), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Survey).order_by(Survey.created_at.desc()))).scalars().all()
    return {"items": [_survey_dict(row) for row in rows]}


@router.patch("/api/admin/surveys/{survey_id}")
async def update_survey(survey_id: str, body: SurveyUpdate, _: AdminContext = Depends(get_admin_ctx),
                        db: AsyncSession = Depends(get_db)):
    survey = await _get_survey(db, survey_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        if key == "questions" and value is not None:
            value = [question.model_dump() for question in body.questions or []]
            value = json.dumps(value, ensure_ascii=False)
            key = "questions_json"
        elif key == "title" and value is not None:
            value = value.strip()
        setattr(survey, key, value)
    survey.updated_at = utc_now()
    await db.commit()
    await db.refresh(survey)
    return _survey_dict(survey)


@router.get("/api/public/{public_id}")
async def public_survey(public_id: str, db: AsyncSession = Depends(get_db)):
    survey = (await db.execute(select(Survey).where(Survey.public_id == public_id))).scalar_one_or_none()
    if survey is None or survey.status != "active":
        raise HTTPException(status_code=404, detail="Survey not found or not active")
    return {"public_id": survey.public_id, "title": survey.title,
            "description": survey.description, "questions": _questions(survey)}


async def _deliver_webhook(event_id: str) -> None:
    async with AsyncSessionLocal() as db:
        event = (await db.execute(select(WebhookEvent).where(WebhookEvent.id == event_id))).scalar_one_or_none()
        if event is None:
            return
        survey = (await db.execute(select(Survey).where(Survey.id == event.survey_id))).scalar_one_or_none()
        if survey is None or not survey.webhook_url:
            event.delivery_status = "not_configured"
            await db.commit()
            return
        try:
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
                result = await client.post(
                    survey.webhook_url,
                    content=event.body.encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-Zigpoll-Signature": event.signature,
                             "X-Zigpoll-Event": "response.submitted"},
                )
            event.response_code = result.status_code
            event.delivery_status = "delivered" if 200 <= result.status_code < 300 else "failed"
            event.delivery_error = None if event.delivery_status == "delivered" else "Endpoint returned a non-success status."
        except httpx.HTTPError:
            event.delivery_status = "failed"
            event.delivery_error = "Network request failed; check the HTTPS endpoint and connectivity."
        await db.commit()


@router.post("/api/public/{public_id}/responses", status_code=status.HTTP_201_CREATED)
async def create_public_response(public_id: str, body: PublicResponseCreate,
                                 background: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    survey = (await db.execute(select(Survey).where(Survey.public_id == public_id))).scalar_one_or_none()
    if survey is None or survey.status != "active":
        raise HTTPException(status_code=404, detail="Survey not found or not active")
    answers = _validate_answers(_questions(survey), body.answers)
    response = SurveyResponse(survey_id=survey.id, respondent_id=body.respondent_id,
                              answers_json=json.dumps(answers, ensure_ascii=False, separators=(",", ":")))
    db.add(response)
    await db.flush()
    payload = {
        "event": "response.submitted",
        "survey_id": survey.public_id,
        "response_id": response.id,
        "submitted_at": response.submitted_at.isoformat(),
        "answers": answers,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    signature = "sha256=" + hmac.new(survey.webhook_secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    event = WebhookEvent(survey_id=survey.id, response_id=response.id, body=raw, signature=signature,
                         delivery_status="pending" if survey.webhook_url else "not_configured")
    db.add(event)
    await db.commit()
    if survey.webhook_url:
        background.add_task(_deliver_webhook, event.id)
    return {"response_id": response.id, "submitted": True}


@router.get("/api/admin/surveys/{survey_id}/responses")
async def list_responses(survey_id: str, _: AdminContext = Depends(get_admin_ctx),
                         db: AsyncSession = Depends(get_db)):
    await _get_survey(db, survey_id)
    rows = (await db.execute(select(SurveyResponse).where(
        SurveyResponse.survey_id == survey_id
    ).order_by(SurveyResponse.submitted_at.asc()))).scalars().all()
    return {"items": [{"id": row.id, "respondent_id": row.respondent_id,
                        "answers": json.loads(row.answers_json),
                        "submitted_at": row.submitted_at.isoformat()} for row in rows]}


@router.get("/api/admin/surveys/{survey_id}/aggregates")
async def survey_aggregates(survey_id: str, _: AdminContext = Depends(get_admin_ctx),
                            db: AsyncSession = Depends(get_db)):
    survey = await _get_survey(db, survey_id)
    rows = (await db.execute(select(SurveyResponse).where(
        SurveyResponse.survey_id == survey_id
    ))).scalars().all()
    questions = _questions(survey)
    result: dict[str, Any] = {question["id"]: {"type": question["type"], "answers": {}, "count": 0}
                              for question in questions}
    for row in rows:
        for qid, value in json.loads(row.answers_json).items():
            entry = result[qid]
            entry["count"] += 1
            if entry["type"] == "rating":
                entry["answers"][str(value)] = entry["answers"].get(str(value), 0) + 1
            elif entry["type"] == "multi_choice":
                for item in value:
                    entry["answers"][item] = entry["answers"].get(item, 0) + 1
            elif entry["type"] == "single_choice":
                entry["answers"][value] = entry["answers"].get(value, 0) + 1
            else:
                entry["answers"]["text_responses"] = entry["answers"].get("text_responses", 0) + 1
    return {"survey_id": survey_id, "response_count": len(rows), "questions": result}


@router.get("/api/admin/surveys/{survey_id}/export.csv")
async def export_csv(survey_id: str, _: AdminContext = Depends(get_admin_ctx),
                     db: AsyncSession = Depends(get_db)):
    survey = await _get_survey(db, survey_id)
    rows = (await db.execute(select(SurveyResponse).where(
        SurveyResponse.survey_id == survey_id
    ).order_by(SurveyResponse.submitted_at.asc()))).scalars().all()
    questions = _questions(survey)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["response_id", "respondent_id", "submitted_at", *[q["label"] for q in questions]])
    for row in rows:
        answers = json.loads(row.answers_json)
        writer.writerow([row.id, row.respondent_id or "", row.submitted_at.isoformat(),
                         *[json.dumps(answers.get(q["id"]), ensure_ascii=False) if isinstance(answers.get(q["id"]), list)
                           else answers.get(q["id"], "") for q in questions]])
    return Response(content=output.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="zigpoll-{survey.public_id}.csv"'})


@router.get("/api/admin/surveys/{survey_id}/webhook-events")
async def list_webhook_events(survey_id: str, _: AdminContext = Depends(get_admin_ctx),
                              db: AsyncSession = Depends(get_db)):
    await _get_survey(db, survey_id)
    rows = (await db.execute(select(WebhookEvent).where(
        WebhookEvent.survey_id == survey_id
    ).order_by(WebhookEvent.created_at.asc()))).scalars().all()
    return {"items": [{"id": row.id, "response_id": row.response_id, "body": row.body,
                        "signature": row.signature, "delivery_status": row.delivery_status,
                        "response_code": row.response_code, "created_at": row.created_at.isoformat()} for row in rows]}


@router.post("/api/webhooks/test/{public_id}")
async def verify_webhook_test(public_id: str, request: Request,
                              x_zigpoll_signature: str | None = Header(default=None),
                              db: AsyncSession = Depends(get_db)):
    survey = (await db.execute(select(Survey).where(Survey.public_id == public_id))).scalar_one_or_none()
    if survey is None:
        raise HTTPException(status_code=404, detail="Survey not found")
    raw = await request.body()
    expected = "sha256=" + hmac.new(survey.webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
    if not x_zigpoll_signature or not hmac.compare_digest(expected, x_zigpoll_signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Webhook body must be JSON") from exc
    return {"accepted": True, "event": parsed.get("event"), "verified": True}


@router.get("/s/{public_id}", include_in_schema=False)
async def public_embed(public_id: str, db: AsyncSession = Depends(get_db)):
    survey = (await db.execute(select(Survey).where(Survey.public_id == public_id))).scalar_one_or_none()
    if survey is None or survey.status != "active":
        raise HTTPException(status_code=404, detail="Survey not found or not active")
    html = f"""<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Zigpoll Survey</title><link rel=\"stylesheet\" href=\"/static/embed.css\"></head><body><main class=\"card\"><div class=\"eyebrow\">ZIGPOLL</div><h1 id=\"title\">Loading survey</h1><p id=\"description\" class=\"muted\"></p><form id=\"survey-form\"></form><div id=\"message\" role=\"status\"></div></main><script>window.ZIGPOLL_PUBLIC_ID={json.dumps(public_id)};</script><script src=\"/static/embed.js\" defer></script></body></html>"""
    return Response(content=html, media_type="text/html; charset=utf-8")

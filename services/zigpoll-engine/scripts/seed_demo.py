"""Populate a local demo database with two surveys and three responses."""
import asyncio
import hashlib
import hmac
import json
import secrets
import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from zigpoll_engine.config import settings
from zigpoll_engine.database import AsyncSessionLocal, init_db
from zigpoll_engine.models import Survey, SurveyResponse, WebhookEvent

SURVEYS = [
    {
        "title": "Post-purchase pulse",
        "description": "A quick check-in about the latest order.",
        "questions": [
            {"id": "rating", "label": "How was your experience?", "type": "rating",
             "required": True, "options": [], "min_value": 1, "max_value": 5},
            {"id": "reason", "label": "What mattered most?", "type": "single_choice",
             "required": True, "options": ["Quality", "Speed", "Support"], "min_value": 1, "max_value": 5},
            {"id": "comment", "label": "Anything else?", "type": "free_text",
             "required": False, "options": [], "min_value": 1, "max_value": 5},
        ],
        "responses": [
            ("demo-001", {"rating": 5, "reason": "Quality", "comment": "Easy to use."}),
            ("demo-002", {"rating": 4, "reason": "Speed", "comment": "Arrived quickly."}),
        ],
    },
    {
        "title": "Feature interest poll",
        "description": "Help us choose what to build next.",
        "questions": [
            {"id": "features", "label": "Which would you use?", "type": "multi_choice",
             "required": True, "options": ["Reports", "Integrations", "Automation"], "min_value": 1, "max_value": 5},
            {"id": "comment", "label": "Why?", "type": "free_text",
             "required": False, "options": [], "min_value": 1, "max_value": 5},
        ],
        "responses": [("demo-003", {"features": ["Reports", "Automation"], "comment": "Less manual work."})],
    },
]


async def seed() -> None:
    await init_db()
    async with AsyncSessionLocal() as db:
        for item in SURVEYS:
            existing = (await db.execute(select(Survey).where(Survey.title == item["title"]))).scalar_one_or_none()
            if existing:
                continue
            survey = Survey(
                public_id="zp_" + secrets.token_urlsafe(12),
                title=item["title"], description=item["description"], status="active",
                questions_json=json.dumps(item["questions"], ensure_ascii=False),
                webhook_secret="zph_" + secrets.token_urlsafe(32),
            )
            db.add(survey)
            await db.flush()
            for respondent_id, answers in item["responses"]:
                response = SurveyResponse(survey_id=survey.id, respondent_id=respondent_id,
                                          answers_json=json.dumps(answers, ensure_ascii=False, separators=(",", ":")))
                db.add(response)
                await db.flush()
                payload = {"event": "response.submitted", "survey_id": survey.public_id,
                           "response_id": response.id, "submitted_at": response.submitted_at.isoformat(),
                           "answers": answers}
                raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                signature = "sha256=" + hmac.new(survey.webhook_secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
                db.add(WebhookEvent(survey_id=survey.id, response_id=response.id,
                                    body=raw, signature=signature))
        await db.commit()
        surveys = (await db.execute(select(Survey).order_by(Survey.title))).scalars().all()
        print(f"Seed complete: {len(surveys)} surveys; demo responses are present; credentials were not printed.")
        for survey in surveys:
            count = (await db.execute(select(SurveyResponse).where(
                SurveyResponse.survey_id == survey.id
            ))).scalars().all()
            print(f"- {survey.title}: public id {survey.public_id}, {len(count)} responses")


if __name__ == "__main__":
    if not settings.ADMIN_PASSWORD:
        raise SystemExit("Set ZIGPOLL_ADMIN_PASSWORD before seeding; no default is provided.")
    asyncio.run(seed())

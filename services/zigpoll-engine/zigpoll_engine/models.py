import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def uid() -> str:
    return str(uuid.uuid4())


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class AdminUser(Base):
    __tablename__ = "admin_users"
    id = Column(String(36), primary_key=True, default=uid)
    email = Column(String(255), unique=True, index=True, nullable=False)
    name = Column(String(120), nullable=False)
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)


class AdminSession(Base):
    __tablename__ = "admin_sessions"
    id = Column(String(36), primary_key=True, default=uid)
    user_id = Column(String(36), ForeignKey("admin_users.id"), nullable=False, index=True)
    token_hash = Column(String(64), unique=True, index=True, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)


class Survey(Base):
    __tablename__ = "surveys"
    id = Column(String(36), primary_key=True, default=uid)
    public_id = Column(String(40), unique=True, index=True, nullable=False)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False, default="")
    status = Column(String(16), nullable=False, default="active")
    webhook_url = Column(String(2048), nullable=True)
    questions_json = Column(Text, nullable=False, default="[]")
    webhook_secret = Column(String(128), nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)


class SurveyResponse(Base):
    __tablename__ = "survey_responses"
    id = Column(String(36), primary_key=True, default=uid)
    survey_id = Column(String(36), ForeignKey("surveys.id"), nullable=False, index=True)
    respondent_id = Column(String(120), nullable=True)
    answers_json = Column(Text, nullable=False)
    submitted_at = Column(DateTime, default=utc_now, nullable=False)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    id = Column(String(36), primary_key=True, default=uid)
    survey_id = Column(String(36), ForeignKey("surveys.id"), nullable=False, index=True)
    response_id = Column(String(36), ForeignKey("survey_responses.id"), nullable=False, index=True)
    body = Column(Text, nullable=False)
    signature = Column(String(80), nullable=False)
    delivery_status = Column(String(20), nullable=False, default="pending")
    response_code = Column(Integer, nullable=True)
    delivery_error = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    __table_args__ = (UniqueConstraint("response_id", name="uq_webhook_response"),)

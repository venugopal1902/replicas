import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
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


class Project(Base):
    __tablename__ = "projects"
    id = Column(String(36), primary_key=True, default=uid)
    name = Column(String(160), nullable=False)
    credits = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)


class ProjectAPIKey(Base):
    __tablename__ = "project_api_keys"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    key_hash = Column(String(64), unique=True, index=True, nullable=False)
    prefix = Column(String(24), nullable=False)
    label = Column(String(120), nullable=False, default="default")
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)


class Template(Base):
    __tablename__ = "templates"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    name = Column(String(160), nullable=False)
    width = Column(Integer, nullable=False)
    height = Column(Integer, nullable=False)
    background = Column(String(7), nullable=False, default="#111827")
    layers_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)


class Asset(Base):
    __tablename__ = "assets"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    mime_type = Column(String(80), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)


class RenderJob(Base):
    __tablename__ = "render_jobs"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    template_id = Column(String(36), ForeignKey("templates.id"), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="queued")
    overrides_json = Column(Text, nullable=False, default="{}")
    output_filename = Column(String(255))
    error = Column(Text)
    credit_cost = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    completed_at = Column(DateTime)


class Usage(Base):
    __tablename__ = "usage"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    job_id = Column(String(36), ForeignKey("render_jobs.id"), nullable=False, index=True)
    credits = Column(Integer, nullable=False)
    balance_after = Column(Integer, nullable=False)
    description = Column(String(200), nullable=False, default="PNG render")
    created_at = Column(DateTime, default=utc_now, nullable=False)


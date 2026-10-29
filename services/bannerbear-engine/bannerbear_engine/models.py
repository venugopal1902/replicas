import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def uid() -> str:
    return str(uuid.uuid4())


class AdminUser(Base):
    __tablename__ = "admin_users"
    id = String(36, primary_key=True, default=uid)
    email = String(255, unique=True, index=True, nullable=False)
    name = String(120, nullable=False)
    password_hash = String(255, nullable=False)
    is_active = Boolean(default=True, nullable=False)
    created_at = DateTime(default=datetime.utcnow, nullable=False)


class AdminSession(Base):
    __tablename__ = "admin_sessions"
    id = String(36, primary_key=True, default=uid)
    user_id = String(36, ForeignKey("admin_users.id"), nullable=False, index=True)
    token_hash = String(64, unique=True, index=True, nullable=False)
    expires_at = DateTime(nullable=False)
    created_at = DateTime(default=datetime.utcnow, nullable=False)


class Project(Base):
    __tablename__ = "projects"
    id = String(36, primary_key=True, default=uid)
    name = String(160, nullable=False)
    credits = Integer(default=0, nullable=False)
    created_at = DateTime(default=datetime.utcnow, nullable=False)


class ProjectAPIKey(Base):
    __tablename__ = "project_api_keys"
    id = String(36, primary_key=True, default=uid)
    project_id = String(36, ForeignKey("projects.id"), nullable=False, index=True)
    key_hash = String(64, unique=True, index=True, nullable=False)
    prefix = String(24, nullable=False)
    label = String(120, nullable=False, default="default")
    is_active = Boolean(default=True, nullable=False)
    created_at = DateTime(default=datetime.utcnow, nullable=False)


class Template(Base):
    __tablename__ = "templates"
    id = String(36, primary_key=True, default=uid)
    project_id = String(36, ForeignKey("projects.id"), nullable=False, index=True)
    name = String(160, nullable=False)
    width = Integer(nullable=False)
    height = Integer(nullable=False)
    background = String(7, nullable=False, default="#111827")
    layers_json = Text(nullable=False)
    created_at = DateTime(default=datetime.utcnow, nullable=False)


class RenderJob(Base):
    __tablename__ = "render_jobs"
    id = String(36, primary_key=True, default=uid)
    project_id = String(36, ForeignKey("projects.id"), nullable=False, index=True)
    template_id = String(36, ForeignKey("templates.id"), nullable=False, index=True)
    status = String(20, nullable=False, default="queued")
    overrides_json = Text(nullable=False, default="{}")
    output_filename = String(255)
    error = Text
    credit_cost = Integer(nullable=False, default=1)
    created_at = DateTime(default=datetime.utcnow, nullable=False)
    completed_at = DateTime


class Usage(Base):
    __tablename__ = "usage"
    id = String(36, primary_key=True, default=uid)
    project_id = String(36, ForeignKey("projects.id"), nullable=False, index=True)
    job_id = String(36, ForeignKey("render_jobs.id"), nullable=False, index=True)
    credits = Integer(nullable=False)
    balance_after = Integer(nullable=False)
    description = String(200, nullable=False, default="PNG render")
    created_at = DateTime(default=datetime.utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("job_id", name="uq_usage_job"),)

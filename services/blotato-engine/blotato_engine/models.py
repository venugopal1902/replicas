import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy import Column, String, Integer, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import declarative_base, relationship
from pydantic import BaseModel, Field

Base = declarative_base()


def uid() -> str:
    return str(uuid.uuid4())


class ApiKeyModel(Base):
    __tablename__ = "api_keys"

    id = Column(String(36), primary_key=True, default=uid)
    api_key = Column(String(128), unique=True, index=True, nullable=False)
    client_name = Column(String(100), nullable=False)
    plan = Column(String(50), default="starter")  # admin, starter, pro, enterprise
    credits = Column(Integer, default=1000)       # -1 for unlimited
    rate_limit_per_hour = Column(Integer, default=1800)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    accounts = relationship("SocialAccountModel", back_populates="user", cascade="all, delete-orphan")
    posts = relationship("PostModel", back_populates="user", cascade="all, delete-orphan")


class SocialAccountModel(Base):
    __tablename__ = "social_accounts"

    id = Column(String(36), primary_key=True, default=uid)
    user_id = Column(String(36), ForeignKey("api_keys.id"), nullable=False, index=True)
    platform = Column(String(50), nullable=False)
    name = Column(String(100), nullable=False)
    username = Column(String(100), nullable=True)
    external_id = Column(String(100), nullable=True)
    credentials_json = Column(Text, default="{}")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("ApiKeyModel", back_populates="accounts")
    posts = relationship("PostModel", back_populates="account")


class PostModel(Base):
    __tablename__ = "posts"

    id = Column(String(36), primary_key=True, default=uid)
    user_id = Column(String(36), ForeignKey("api_keys.id"), nullable=False, index=True)
    account_id = Column(String(36), ForeignKey("social_accounts.id"), nullable=False, index=True)
    platform = Column(String(50), nullable=False)
    text = Column(Text, nullable=False)
    media_urls_json = Column(Text, default="[]")
    status = Column(String(30), default="in-progress")  # in-progress, published, scheduled, failed, draft
    scheduled_time = Column(DateTime, nullable=True, index=True)
    published_at = Column(DateTime, nullable=True)
    platform_post_id = Column(String(120), nullable=True)
    platform_post_url = Column(String(500), nullable=True)
    error_message = Column(Text, nullable=True)
    target_meta_json = Column(Text, default="{}")
    retry_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("ApiKeyModel", back_populates="posts")
    account = relationship("SocialAccountModel", back_populates="posts")


class DMAutomationModel(Base):
    __tablename__ = "dm_automations"

    id = Column(String(36), primary_key=True, default=uid)
    user_id = Column(String(36), ForeignKey("api_keys.id"), nullable=False, index=True)
    account_id = Column(String(36), nullable=False)
    platform = Column(String(50), nullable=False)
    name = Column(String(100), nullable=False)
    dm_message = Column(String(640), nullable=False)
    triggers_json = Column(Text, default="[]")
    buttons_json = Column(Text, default="[]")
    comment_reply_variants_json = Column(Text, default="[]")
    follow_gate_json = Column(Text, default="{}")
    email_gate_json = Column(Text, default="{}")
    webhook_json = Column(Text, default="{}")
    is_active = Column(Boolean, default=True)
    triggered_count = Column(Integer, default=0)
    completed_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    last_run_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class MediaAssetModel(Base):
    __tablename__ = "media_assets"

    id = Column(String(36), primary_key=True, default=uid)
    user_id = Column(String(36), ForeignKey("api_keys.id"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    content_type = Column(String(120), nullable=False)
    size_bytes = Column(Integer, default=0)
    public_url = Column(String(700), nullable=False)
    storage_path = Column(String(700), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ScheduleSlotModel(Base):
    __tablename__ = "schedule_slots"

    id = Column(String(36), primary_key=True, default=uid)
    user_id = Column(String(36), ForeignKey("api_keys.id"), nullable=False, index=True)
    account_id = Column(String(36), nullable=False, index=True)
    platform = Column(String(50), nullable=False)
    day_of_week = Column(Integer, default=1)  # 0=Monday .. 6=Sunday
    hour = Column(Integer, default=9)
    minute = Column(Integer, default=0)
    timezone = Column(String(60), default="Asia/Kolkata")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class CommentModel(Base):
    __tablename__ = "comments"

    id = Column(String(36), primary_key=True, default=uid)
    user_id = Column(String(36), ForeignKey("api_keys.id"), nullable=False, index=True)
    account_id = Column(String(36), nullable=False, index=True)
    platform = Column(String(50), nullable=False)
    platform_post_id = Column(String(120), nullable=True)
    author = Column(String(120), nullable=True)
    author_avatar = Column(String(500), nullable=True)
    text = Column(Text, nullable=False)
    is_replied = Column(Boolean, default=False)
    reply_text = Column(Text, nullable=True)
    replied_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class MessageModel(Base):
    __tablename__ = "messages"

    id = Column(String(36), primary_key=True, default=uid)
    user_id = Column(String(36), ForeignKey("api_keys.id"), nullable=False, index=True)
    account_id = Column(String(36), nullable=False, index=True)
    platform = Column(String(50), nullable=False)
    thread_id = Column(String(120), nullable=False, index=True)
    author = Column(String(120), nullable=True)
    text = Column(Text, nullable=False)
    direction = Column(String(20), default="inbound")  # inbound | outbound
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class AnalyticsSnapshotModel(Base):
    __tablename__ = "analytics_snapshots"

    id = Column(String(36), primary_key=True, default=uid)
    user_id = Column(String(36), ForeignKey("api_keys.id"), nullable=False, index=True)
    post_submission_id = Column(String(36), nullable=False, index=True)
    platform = Column(String(50), nullable=False)
    views = Column(Integer, default=0)
    likes = Column(Integer, default=0)
    comments = Column(Integer, default=0)
    shares = Column(Integer, default=0)
    saves = Column(Integer, default=0)
    reach = Column(Integer, default=0)
    captured_at = Column(DateTime, default=datetime.utcnow)


class VisualModel(Base):
    __tablename__ = "visuals"

    id = Column(String(36), primary_key=True, default=uid)
    user_id = Column(String(36), ForeignKey("api_keys.id"), nullable=False, index=True)
    template_id = Column(String(120), nullable=False)
    template_title = Column(String(200), nullable=True)
    status = Column(String(30), default="queued")  # queued, rendering, ready, failed
    inputs_json = Column(Text, default="{}")
    output_urls_json = Column(Text, default="[]")
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


class SourceModel(Base):
    __tablename__ = "sources"

    id = Column(String(36), primary_key=True, default=uid)
    user_id = Column(String(36), ForeignKey("api_keys.id"), nullable=False, index=True)
    source_type = Column(String(50), nullable=False)  # youtube, tiktok, article, pdf, audio, text, x
    source_url = Column(String(1000), nullable=True)
    status = Column(String(30), default="processing")
    extracted_json = Column(Text, default="{}")
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class WebhookEventModel(Base):
    __tablename__ = "webhook_events"

    id = Column(String(36), primary_key=True, default=uid)
    user_id = Column(String(36), nullable=True, index=True)
    automation_id = Column(String(36), nullable=True, index=True)
    event_type = Column(String(60), nullable=False)
    payload_json = Column(Text, default="{}")
    status = Column(String(30), default="received")  # received, matched, no_match, delivered, failed
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------- schemas

class PostContent(BaseModel):
    text: str
    mediaUrls: Optional[List[str]] = Field(default_factory=list)
    platform: Optional[str] = None


class CreatePostRequest(BaseModel):
    post: Dict[str, Any]
    scheduledTime: Optional[str] = None


class AccountResponse(BaseModel):
    id: str
    platform: str
    name: str
    username: Optional[str] = None
    isActive: bool = True


class PostResponse(BaseModel):
    postSubmissionId: str
    status: str
    platform: str
    accountId: str
    text: str
    mediaUrls: List[str] = []
    scheduledTime: Optional[str] = None
    publishedAt: Optional[str] = None
    url: Optional[str] = None
    errorMessage: Optional[str] = None

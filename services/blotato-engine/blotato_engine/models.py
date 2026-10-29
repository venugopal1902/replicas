import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy import Column, String, Integer, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import declarative_base, relationship
from pydantic import BaseModel, Field

Base = declarative_base()

class ApiKeyModel(Base):
    __tablename__ = "api_keys"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    api_key = Column(String(128), unique=True, index=True, nullable=False)
    client_name = Column(String(100), nullable=False)
    plan = Column(String(50), default="starter")  # "admin", "starter", "pro", "enterprise"
    credits = Column(Integer, default=1000)       # -1 for unlimited
    rate_limit_per_hour = Column(Integer, default=1800)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    accounts = relationship("SocialAccountModel", back_populates="user", cascade="all, delete-orphan")
    posts = relationship("PostModel", back_populates="user", cascade="all, delete-orphan")

class SocialAccountModel(Base):
    __tablename__ = "social_accounts"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("api_keys.id"), nullable=False, index=True)
    platform = Column(String(50), nullable=False)  # instagram, facebook, twitter, linkedin, youtube, threads, bluesky
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
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("api_keys.id"), nullable=False, index=True)
    account_id = Column(String(36), ForeignKey("social_accounts.id"), nullable=False, index=True)
    platform = Column(String(50), nullable=False)
    text = Column(Text, nullable=False)
    media_urls_json = Column(Text, default="[]")
    status = Column(String(30), default="in-progress")  # in-progress, published, scheduled, failed
    scheduled_time = Column(DateTime, nullable=True, index=True)
    published_at = Column(DateTime, nullable=True)
    platform_post_id = Column(String(120), nullable=True)
    platform_post_url = Column(String(500), nullable=True)
    error_message = Column(Text, nullable=True)
    target_meta_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.utcnow)
    
    user = relationship("ApiKeyModel", back_populates="posts")
    account = relationship("SocialAccountModel", back_populates="posts")

class DMAutomationModel(Base):
    __tablename__ = "dm_automations"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("api_keys.id"), nullable=False, index=True)
    account_id = Column(String(36), nullable=False)
    platform = Column(String(50), nullable=False)
    name = Column(String(100), nullable=False)
    dm_message = Column(String(640), nullable=False)
    triggers_json = Column(Text, default="[]")
    buttons_json = Column(Text, default="[]")
    comment_reply_variants_json = Column(Text, default="[]")
    is_active = Column(Boolean, default=True)
    triggered_count = Column(Integer, default=0)
    completed_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Schemas matching Blotato API
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

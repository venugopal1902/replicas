import uuid
from datetime import datetime, date

from sqlalchemy import (
    Column, String, Integer, Boolean, DateTime, Date, Text, Float, ForeignKey, Index,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def uid() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=uid)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    name = Column(String(120), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Workspace(Base):
    __tablename__ = "workspaces"

    id = Column(String(36), primary_key=True, default=uid)
    name = Column(String(160), nullable=False, default="My workspace")
    owner_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    plan = Column(String(40), default="professional")   # free|basic|professional|premium
    credits = Column(Integer, default=5000)             # reveal credits
    verifier_credits = Column(Integer, default=10000)   # separate pool
    seats = Column(Integer, default=3)
    recipients_limit = Column(Integer, default=10000)   # campaign recipients / month
    recipients_used = Column(Integer, default=0)
    api_calls_limit = Column(Integer, default=1500)     # per day
    created_at = Column(DateTime, default=datetime.utcnow)


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"

    id = Column(String(36), primary_key=True, default=uid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    role = Column(String(30), default="owner")   # owner|admin|member
    created_at = Column(DateTime, default=datetime.utcnow)


class Company(Base):
    __tablename__ = "companies"

    id = Column(String(36), primary_key=True, default=uid)
    name = Column(String(200), nullable=False, index=True)
    domain = Column(String(200), index=True)
    industry = Column(String(120))
    headcount = Column(String(40))          # e.g. "51-200"
    location = Column(String(160))
    linkedin_url = Column(String(400))
    founded_year = Column(Integer)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class Contact(Base):
    __tablename__ = "contacts"

    id = Column(String(36), primary_key=True, default=uid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)

    first_name = Column(String(100), index=True)
    last_name = Column(String(100), index=True)
    title = Column(String(200), index=True)
    seniority = Column(String(60))           # derived: founder|vp|head|director|manager|ic
    company_name = Column(String(200), index=True)
    company_id = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    domain = Column(String(200), index=True)
    industry = Column(String(120), index=True)
    headcount = Column(String(40), index=True)
    location = Column(String(160), index=True)
    linkedin_url = Column(String(400), index=True)
    years_experience = Column(Integer, nullable=True)

    # work email
    work_email = Column(String(255))
    work_email_status = Column(String(20))    # verified|risky|catch_all|invalid|unknown
    work_email_score = Column(String(4))      # A+|A|B|C
    work_email_pattern = Column(String(60))

    # direct / personal email
    direct_email = Column(String(255))
    direct_email_status = Column(String(20))
    direct_email_score = Column(String(4))

    # phone
    phone = Column(String(40))
    phone_source = Column(String(60))

    revealed = Column(Boolean, default=False)
    source = Column(String(40), default="database")   # database|linkedin|csv|api|manual
    enriched_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_contact_workspace_linkedin", "workspace_id", "linkedin_url"),
    )


class Folder(Base):
    __tablename__ = "folders"

    id = Column(String(36), primary_key=True, default=uid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    name = Column(String(160), nullable=False)
    color = Column(String(20), default="#6ee7b7")
    created_by = Column(String(36), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class FolderContact(Base):
    __tablename__ = "folder_contacts"

    id = Column(String(36), primary_key=True, default=uid)
    folder_id = Column(String(36), ForeignKey("folders.id"), nullable=False, index=True)
    contact_id = Column(String(36), ForeignKey("contacts.id"), nullable=False, index=True)
    added_by = Column(String(36), nullable=True)
    added_at = Column(DateTime, default=datetime.utcnow)


class CreditEvent(Base):
    __tablename__ = "credit_events"

    id = Column(String(36), primary_key=True, default=uid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    user_id = Column(String(36), nullable=True)
    delta = Column(Integer, nullable=False)          # negative for spend
    pool = Column(String(20), default="reveal")      # reveal|verifier|recipient
    reason = Column(String(120), nullable=False)
    contact_id = Column(String(36), nullable=True)
    balance_after = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class SearchLog(Base):
    __tablename__ = "searches"

    id = Column(String(36), primary_key=True, default=uid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    user_id = Column(String(36), nullable=True)
    prompt = Column(Text)
    filters_json = Column(Text, default="{}")
    result_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class EnrichmentJob(Base):
    __tablename__ = "enrichment_jobs"

    id = Column(String(36), primary_key=True, default=uid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    user_id = Column(String(36), nullable=True)
    job_type = Column(String(30), nullable=False)     # csv_url|csv_lead|csv_email|linkedin_urls|identity|reverse_email
    status = Column(String(20), default="queued")     # queued|running|done|failed
    total = Column(Integer, default=0)
    processed = Column(Integer, default=0)
    found = Column(Integer, default=0)
    credits_spent = Column(Integer, default=0)
    input_path = Column(String(600))
    output_path = Column(String(600))
    error = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)


class EmailVerification(Base):
    __tablename__ = "email_verifications"

    id = Column(String(36), primary_key=True, default=uid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    email = Column(String(255), nullable=False, index=True)
    result = Column(String(20))        # valid|invalid|risky|catch_all|unknown
    score = Column(String(4))
    mx_host = Column(String(255))
    reason = Column(String(255))
    credits_spent = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(String(36), primary_key=True, default=uid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    user_id = Column(String(36), nullable=True)
    key = Column(String(80), unique=True, index=True, nullable=False)
    label = Column(String(120), default="default")
    calls_today = Column(Integer, default=0)
    calls_limit = Column(Integer, default=1500)
    last_reset = Column(Date, default=date.today)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Session(Base):
    __tablename__ = "sessions"

    id = Column(String(36), primary_key=True, default=uid)
    token = Column(String(80), unique=True, index=True, nullable=False)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    workspace_id = Column(String(36), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)


class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(String(36), primary_key=True, default=uid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    status = Column(String(20), default="draft")     # draft|active|paused|done
    from_email = Column(String(255))
    from_name = Column(String(120))
    daily_limit = Column(Integer, default=50)
    created_at = Column(DateTime, default=datetime.utcnow)


class CampaignStep(Base):
    __tablename__ = "campaign_steps"

    id = Column(String(36), primary_key=True, default=uid)
    campaign_id = Column(String(36), ForeignKey("campaigns.id"), nullable=False, index=True)
    position = Column(Integer, default=1)
    subject = Column(String(300))
    body = Column(Text)
    delay_days = Column(Integer, default=0)


class CampaignRecipient(Base):
    __tablename__ = "campaign_recipients"

    id = Column(String(36), primary_key=True, default=uid)
    campaign_id = Column(String(36), ForeignKey("campaigns.id"), nullable=False, index=True)
    contact_id = Column(String(36), nullable=True, index=True)
    email = Column(String(255), nullable=False)
    name = Column(String(200))
    status = Column(String(20), default="queued")    # queued|sent|bounced|replied|unsubscribed
    current_step = Column(Integer, default=0)
    tracking_token = Column(String(40), unique=True, index=True)
    sent_count = Column(Integer, default=0)
    open_count = Column(Integer, default=0)
    click_count = Column(Integer, default=0)
    last_event_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class CampaignEvent(Base):
    __tablename__ = "campaign_events"

    id = Column(String(36), primary_key=True, default=uid)
    recipient_id = Column(String(36), ForeignKey("campaign_recipients.id"), nullable=False, index=True)
    campaign_id = Column(String(36), nullable=False, index=True)
    step_position = Column(Integer, default=1)
    event_type = Column(String(20), nullable=False)  # sent|open|click|bounce|reply|unsubscribe
    meta_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.utcnow)

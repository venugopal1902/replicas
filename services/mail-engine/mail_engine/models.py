"""Mail Engine schema: workspace, mailboxes, campaigns, sequences, leads, sends, inbox, warmup."""
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


class Session(Base):
    __tablename__ = "sessions"

    token = Column(String(80), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    workspace_id = Column(String(36), ForeignKey("workspaces.id"), nullable=False)
    expires_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)


class Workspace(Base):
    __tablename__ = "workspaces"

    id = Column(String(36), primary_key=True, default=uid)
    name = Column(String(160), nullable=False, default="My workspace")
    owner_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    plan = Column(String(40), default="professional")     # starter|professional|agency
    daily_limit = Column(Integer, default=200)            # workspace-wide emails per day
    sending_domain = Column(String(160), default="mailengine.local")
    warmup_enabled = Column(Boolean, default=True)
    timezone = Column(String(60), default="Asia/Kolkata")
    created_at = Column(DateTime, default=datetime.utcnow)


class Mailbox(Base):
    __tablename__ = "mailboxes"

    id = Column(String(36), primary_key=True, default=uid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    email = Column(String(255), nullable=False, index=True)
    display_name = Column(String(120))
    provider = Column(String(40), default="simulated")     # simulated|smtp|google|microsoft
    status = Column(String(20), default="active")          # active|paused|error
    daily_limit = Column(Integer, default=50)
    sent_today = Column(Integer, default=0)
    last_reset = Column(Date, default=date.today)
    warmup_score = Column(Float, default=0.0)              # 0-100
    warmup_enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(String(36), primary_key=True, default=uid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    subject = Column(String(300), default="")
    body = Column(Text, default="")
    status = Column(String(20), default="draft", index=True)  # draft|active|paused|completed
    daily_limit = Column(Integer, default=100)
    stop_on_reply = Column(Boolean, default=True)
    from_mailbox_id = Column(String(36), ForeignKey("mailboxes.id"), nullable=True)
    tracking_enabled = Column(Boolean, default=True)
    send_window_start = Column(Integer, default=9)          # local hour
    send_window_end = Column(Integer, default=17)
    created_at = Column(DateTime, default=datetime.utcnow)
    activated_at = Column(DateTime, nullable=True)


class SequenceStep(Base):
    __tablename__ = "sequence_steps"

    id = Column(String(36), primary_key=True, default=uid)
    campaign_id = Column(String(36), ForeignKey("campaigns.id"), nullable=False, index=True)
    step_order = Column(Integer, default=1)
    delay_days = Column(Integer, default=0)
    subject = Column(String(300), default="")
    body = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class Lead(Base):
    __tablename__ = "leads"

    id = Column(String(36), primary_key=True, default=uid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    email = Column(String(255), nullable=False, index=True)
    first_name = Column(String(120))
    last_name = Column(String(120))
    company = Column(String(200))
    title = Column(String(200))
    domain = Column(String(200))
    status = Column(String(30), default="new", index=True)  # new|contacted|opened|replied|bounced|unsubscribed
    source = Column(String(60), default="manual")
    created_at = Column(DateTime, default=datetime.utcnow)


class Enrollment(Base):
    __tablename__ = "enrollments"

    id = Column(String(36), primary_key=True, default=uid)
    campaign_id = Column(String(36), ForeignKey("campaigns.id"), nullable=False, index=True)
    lead_id = Column(String(36), ForeignKey("leads.id"), nullable=False, index=True)
    status = Column(String(20), default="active", index=True)  # active|paused|finished|replied|bounced
    current_step = Column(Integer, default=0)                  # steps already sent
    next_send_at = Column(DateTime, default=datetime.utcnow)
    last_sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Send(Base):
    __tablename__ = "sends"

    id = Column(String(36), primary_key=True, default=uid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    campaign_id = Column(String(36), ForeignKey("campaigns.id"), nullable=True, index=True)
    lead_id = Column(String(36), ForeignKey("leads.id"), nullable=True, index=True)
    mailbox_id = Column(String(36), ForeignKey("mailboxes.id"), nullable=True)
    step_order = Column(Integer, default=1)
    to_email = Column(String(255), index=True)
    subject = Column(String(300))
    body = Column(Text)
    status = Column(String(20), default="sent", index=True)    # sent|blocked|bounced
    blocked_reason = Column(String(120), nullable=True)
    tracking_id = Column(String(48), index=True)
    direction = Column(String(10), default="outbound")          # outbound|inbound
    sent_at = Column(DateTime, default=datetime.utcnow)
    opened_at = Column(DateTime, nullable=True)
    clicked_at = Column(DateTime, nullable=True)
    replied_at = Column(DateTime, nullable=True)
    bounced_at = Column(DateTime, nullable=True)


class InboxMessage(Base):
    __tablename__ = "inbox_messages"

    id = Column(String(36), primary_key=True, default=uid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    mailbox_id = Column(String(36), ForeignKey("mailboxes.id"), nullable=True)
    lead_id = Column(String(36), ForeignKey("leads.id"), nullable=True, index=True)
    campaign_id = Column(String(36), ForeignKey("campaigns.id"), nullable=True)
    from_email = Column(String(255))
    from_name = Column(String(160))
    subject = Column(String(300))
    body = Column(Text)
    classification = Column(String(30), default="interested")  # interested|not_now|ooo|unsubscribe|bounce
    is_read = Column(Boolean, default=False, index=True)
    received_at = Column(DateTime, default=datetime.utcnow)


class Suppression(Base):
    __tablename__ = "suppressions"

    id = Column(String(36), primary_key=True, default=uid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    email = Column(String(255), index=True)
    reason = Column(String(120), default="manual")   # manual|unsubscribe|bounce|complaint
    created_at = Column(DateTime, default=datetime.utcnow)


class WarmupDay(Base):
    __tablename__ = "warmup_days"

    id = Column(String(36), primary_key=True, default=uid)
    mailbox_id = Column(String(36), ForeignKey("mailboxes.id"), nullable=False, index=True)
    day = Column(Date, default=date.today)
    sent = Column(Integer, default=0)
    received = Column(Integer, default=0)
    landed_inbox = Column(Integer, default=0)
    spam = Column(Integer, default=0)
    score = Column(Float, default=0.0)


Index("ix_enroll_campaign_lead", Enrollment.campaign_id, Enrollment.lead_id)
Index("ix_send_campaign_status", Send.campaign_id, Send.status)

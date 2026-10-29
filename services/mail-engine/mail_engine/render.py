"""Merge tags, HTML rendering, tracking artefacts and unsubscribe tokens."""
import hashlib
import hmac
import html
import re
import secrets
from typing import Mapping

from .config import settings

MERGE_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

KNOWN_FIELDS = (
    "first_name", "last_name", "company", "title", "email", "domain", "sender_name",
)

DEFAULTS = {
    "first_name": "there",
    "last_name": "",
    "company": "your team",
    "title": "",
    "email": "",
    "domain": "",
    "sender_name": "Venu",
}

PIXEL_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!"
    b"\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00"
    b"\x00\x02\x02D\x01\x00;"
)


def merge_fields(overrides: Mapping[str, str] | None = None) -> dict:
    fields = dict(DEFAULTS)
    if overrides:
        for key, value in overrides.items():
            if value:
                fields[key] = str(value)
    return fields


def render(template: str, fields: Mapping[str, str]) -> str:
    """Replace {{tag}} placeholders. Unknown tags render empty rather than leaking braces."""
    if not template:
        return ""
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in fields.items() if v is not None})

    def _sub(match: "re.Match[str]") -> str:
        key = match.group(1)
        return str(merged.get(key, ""))

    return MERGE_RE.sub(_sub, template)


def used_tags(template: str) -> list[str]:
    seen: list[str] = []
    for match in MERGE_RE.finditer(template or ""):
        tag = match.group(1)
        if tag not in seen:
            seen.append(tag)
    return seen


def unknown_tags(template: str) -> list[str]:
    return [t for t in used_tags(template) if t not in KNOWN_FIELDS]


def text_to_html(body: str) -> str:
    """Plain-text body to safe HTML: paragraphs, single newlines to <br>."""
    safe = html.escape(body or "")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", safe) if p.strip()]
    return "".join(f"<p>{p.replace(chr(10), '<br>')}</p>" for p in paragraphs)


def build_html(body: str, *, tracking_id: str | None = None, unsubscribe_email: str | None = None) -> str:
    inner = text_to_html(body)
    footer = ""
    if unsubscribe_email:
        link = unsubscribe_url(unsubscribe_email)
        footer = (
            '<p style="margin-top:24px;font-size:12px;color:#64748b">'
            f'You are receiving this because we think it is relevant. '
            f'<a href="{html.escape(link)}">Unsubscribe</a>.</p>'
        )
    pixel = ""
    if tracking_id:
        pixel = (
            f'<img src="{settings.PUBLIC_BASE_URL}/api/tracking/open/{tracking_id}.gif" '
            'width="1" height="1" alt="" style="display:none" />'
        )
    return (
        '<!doctype html><html><body style="font-family:Arial,Helvetica,sans-serif;'
        'font-size:15px;line-height:1.55;color:#0f172a;max-width:640px">'
        f"{inner}{footer}{pixel}</body></html>"
    )


def new_tracking_id() -> str:
    return secrets.token_hex(12)


def unsubscribe_token(email: str) -> str:
    digest = hmac.new(
        settings.UNSUBSCRIBE_SECRET.encode(), email.lower().strip().encode(), hashlib.sha256
    ).hexdigest()[:32]
    return digest


def unsubscribe_url(email: str) -> str:
    return f"{settings.PUBLIC_BASE_URL}/api/unsubscribe/{unsubscribe_token(email)}?email={email}"


def verify_unsubscribe_token(email: str, token: str) -> bool:
    return hmac.compare_digest(unsubscribe_token(email), token or "")


def step_length_ok(body: str, limit: int = 1200) -> bool:
    return len(body or "") <= limit


def subject_score(subject: str) -> dict:
    """Cheap spam-ish heuristics used by the UI quality meter."""
    text = (subject or "").strip()
    issues = []
    if not text:
        issues.append("empty")
    if len(text) > 78:
        issues.append("too long")
    if text.isupper() and text:
        issues.append("all caps")
    if text.count("!") > 1:
        issues.append("excess punctuation")
    spam_words = ("free", "guarantee", "act now", "limited time", "100%", "risk-free")
    lowered = text.lower()
    for word in spam_words:
        if word in lowered:
            issues.append(f"spam word: {word}")
    score = max(0, 100 - 18 * len(issues))
    return {"score": score, "issues": issues, "length": len(text)}

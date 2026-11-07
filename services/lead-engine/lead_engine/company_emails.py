"""Company email discovery, exposed to the API and the UI.

Reads a company's own public pages, extracts the addresses it publishes, adds the standard role
inboxes, resolves MX, and optionally asks the mail server whether each mailbox exists.

Kept separate from the CLI script so the API, the UI and the script all share one implementation.
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Dict, List, Optional

import httpx

from . import enrichment

UA = "Mozilla/5.0 (compatible; lead-engine/1.0)"
PATHS = ["/", "/contact", "/contact-us", "/about", "/about-us", "/team", "/people", "/press", "/careers"]
ROLES = ["info", "hello", "contact", "sales", "support", "careers", "jobs", "press", "team"]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
OBFUSCATED_RE = re.compile(
    r"([A-Za-z0-9._%+\-]+)\s*(?:\[at\]|\(at\)|\sat\s)\s*([A-Za-z0-9.\-]+\s*(?:\[dot\]|\(dot\)|\.)\s*[A-Za-z]{2,})",
    re.IGNORECASE,
)


def normalise_domain(raw: str) -> str:
    value = (raw or "").strip().lower()
    for prefix in ("https://", "http://"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    return value.split("/")[0].strip(".")


async def _fetch(client: httpx.AsyncClient, url: str) -> str:
    try:
        response = await client.get(url, timeout=12.0, follow_redirects=True)
        if response.status_code != 200:
            return ""
        return response.text[:400_000]
    except Exception:
        return ""


async def _robots_allows(client: httpx.AsyncClient, base: str, path: str) -> bool:
    robots = await _fetch(client, base + "/robots.txt")
    if not robots:
        return True
    for line in robots.splitlines():
        lowered = line.lower().strip()
        if lowered.startswith("disallow:"):
            rule = line.split(":", 1)[1].strip()
            if rule == "/" or (rule and path.startswith(rule)):
                return False
    return True


def _extract(html: str, domain: str) -> Dict[str, str]:
    found: Dict[str, str] = {}
    for match in re.finditer(r'href="mailto:([^"?]+)', html, re.I):
        address = urllib.parse.unquote(match.group(1)).strip().lower()
        if address.endswith("@" + domain):
            found.setdefault(address, "mailto link")
    for match in EMAIL_RE.finditer(html):
        address = match.group(0).lower().strip(".,;:")
        if address.endswith("@" + domain):
            found.setdefault(address, "page text")
    for match in OBFUSCATED_RE.finditer(html):
        local = match.group(1).lower()
        host = re.sub(r"\s*(?:\[dot\]|\(dot\))\s*", ".", match.group(2), flags=re.I).strip()
        address = f"{local}@{host}".replace(" ", "")
        if address.endswith("@" + domain):
            found.setdefault(address, "obfuscated text")
    return found


async def scan(domain: str, smtp: bool = False) -> dict:
    """Return published emails, role addresses, MX hosts and (optionally) SMTP verdicts."""
    domain = normalise_domain(domain)
    if not domain or "." not in domain:
        raise ValueError("Provide a domain such as stripe.com")

    base = f"https://{domain}"
    notes: List[str] = []
    pages: List[str] = []

    mx_hosts = await enrichment.mx_lookup(domain)
    if not mx_hosts:
        return {
            "domain": domain, "mx": [], "pagesRead": [], "emails": [],
            "notes": ["no MX records: this domain cannot receive mail"],
        }

    published: Dict[str, str] = {}
    async with httpx.AsyncClient(headers={"User-Agent": UA, "Accept": "text/html,*/*"}) as client:
        for path in PATHS:
            if not await _robots_allows(client, base, path):
                notes.append(f"robots.txt disallows {path}")
                continue
            html = await _fetch(client, base + path)
            if not html:
                continue
            pages.append(path)
            for address, how in _extract(html, domain).items():
                published.setdefault(address, f"{how} on {path}")

    emails: List[dict] = [
        {"email": address, "source": how, "constructed": False} for address, how in sorted(published.items())
    ]
    seen = {item["email"] for item in emails}
    for role in ROLES:
        address = f"{role}@{domain}"
        if address not in seen:
            emails.append({"email": address, "source": "role address (constructed)", "constructed": True})
            seen.add(address)

    if smtp:
        for item in emails:
            verdict = await enrichment.verify_email(item["email"], use_smtp=True)
            item["status"] = verdict.get("result") or verdict.get("status")
            item["score"] = verdict.get("score")
            item["reason"] = verdict.get("reason")

    return {"domain": domain, "mx": mx_hosts, "pagesRead": pages, "notes": notes, "emails": emails}

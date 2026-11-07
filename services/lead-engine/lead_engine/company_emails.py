"""Company email discovery, exposed to the API and the UI.

Reads a company's own public pages, extracts the addresses it publishes, adds the standard role
inboxes, resolves MX, and optionally asks the mail server whether each mailbox exists.

Kept separate from the CLI script so the API, the UI and the script all share one implementation.
"""
from __future__ import annotations

import re
import urllib.parse
import asyncio
import json
from typing import Dict, List, Optional

import httpx

from . import enrichment

UA = "Mozilla/5.0 (compatible; lead-engine/1.0)"
PATHS = ["/", "/contact", "/contact-us", "/about", "/about-us", "/team", "/our-team", "/people",
         "/our-people", "/leadership", "/management", "/company", "/company/team", "/company/leadership",
         "/who-we-are", "/press", "/careers"]
TEAM_HINTS = ("team", "people", "leadership", "management", "founder", "about", "company", "who-we-are")
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


# ---------------------------------------------------------------------------
# People: role addresses are not people. Harvest real names from the company's
# own pages, then build and verify that person's address.
# ---------------------------------------------------------------------------

NAME_RE = re.compile(r"^[A-Z][a-z'\-]{1,20}(?:\s+[A-Z][a-z'\-]{1,20}){1,2}$")
TITLE_WORDS = (
    "ceo", "cto", "coo", "cfo", "cio", "founder", "co-founder", "president", "owner",
    "partner", "director", "manager", "engineer", "architect", "developer", "designer",
    "recruiter", "officer", "consultant", "analyst", "producer", "head of", "vp",
)
# Words that appear in Title Case marketing headings and are never somebody's name.
NOT_NAMES = {
    "engineering", "services", "solutions", "expansion", "global", "about", "contact", "team",
    "products", "platform", "company", "careers", "press", "marketing", "sales", "support",
    "digital", "cloud", "data", "analytics", "technology", "software", "consulting", "partner",
    "partners", "clients", "customers", "resources", "insights", "leadership", "management",
    "delivering", "precision", "driven", "scale", "growth", "strategy", "operations", "security",
    "compliance", "migration", "modernisation", "modernization", "transformation", "value",
}
_TITLE_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in TITLE_WORDS) + r")\b", re.I)
PERSON_PATTERNS = [("first.last", "{first}.{last}"), ("firstlast", "{first}{last}"),
                   ("f.last", "{f}.{last}"), ("first", "{first}")]
MAX_PEOPLE = 8
MAX_PATTERNS = 3


def _clean_name(raw: str) -> tuple[str, str]:
    """Split a display name into (first, last), dropping credentials and emoji."""
    text = re.sub(r"[^A-Za-z .'\-]", " ", raw or "")
    parts = [p for p in text.split() if p]
    if len(parts) < 2:
        return (parts[0] if parts else ""), ""
    return parts[0], parts[-1]


def _name_from_linkedin_slug(slug: str) -> str:
    stem = re.sub(r"-?[0-9a-f]{6,}$", "", slug or "", flags=re.I)
    words = [w for w in re.split(r"[-_]", stem) if w.isalpha() and len(w) > 1]
    if len(words) < 2:
        return ""
    return " ".join(w.capitalize() for w in words[:3])


def _extract_people(html: str) -> List[dict]:
    """Names from JSON-LD Person entries, LinkedIn profile links and team-card headings."""
    people: List[dict] = []
    seen: set[str] = set()

    def add(name: str, title: str, how: str) -> None:
        first, last = _clean_name(name)
        if not first or not last:
            return
        key = f"{first} {last}".lower()
        if key in seen:
            return
        lowered_title = (title or "").lower()
        if not _TITLE_RE.search(lowered_title):
            # keep a bare name only when it looked like a heading (team card), not nav text
            if how != "team card":
                return
        seen.add(key)
        people.append({"name": f"{first} {last}", "first": first, "last": last,
                       "title": (title or "").strip()[:80], "source": how})

    # 1. JSON-LD
    for block in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            data = json.loads(block.group(1))
        except Exception:
            continue
        candidates = data if isinstance(data, list) else [data]
        for entry in candidates:
            if not isinstance(entry, dict):
                continue
            graph = entry.get("@graph")
            if isinstance(graph, list):
                candidates.extend([g for g in graph if isinstance(g, dict)])
            if str(entry.get("@type", "")).lower() != "person":
                continue
            add(str(entry.get("name") or ""), str(entry.get("jobTitle") or ""), "structured data")

    # 2. LinkedIn profile links: the slug is usually the person's name
    for match in re.finditer(r'href="[^"]*linkedin\.com/in/([^"?/]+)[^"]*"[^>]*>(.*?)</a>', html, re.S | re.I):
        slug, anchor = match.group(1), re.sub(r"<[^>]+>", " ", match.group(2))
        from_anchor = re.sub(r"\s+", " ", anchor).strip()
        name = from_anchor if NAME_RE.match(from_anchor) else _name_from_linkedin_slug(slug)
        if name:
            add(name, "see profile", "linkedin link")

    # 3. Team cards: a heading followed by a title-ish line
    for match in re.finditer(r"<(h[2-5])[^>]*>(.*?)</\1>((?:.|\n)*?)(?=<h[2-5]|$)", html, re.S | re.I):
        heading = re.sub(r"<[^>]+>", " ", match.group(2))
        heading = re.sub(r"\s+", " ", html_module_unescape(heading)).strip()
        if not NAME_RE.match(heading):
            continue
        words = [w.lower().strip(".'-") for w in heading.split()]
        if any(w in NOT_NAMES for w in words) or len(words) > 3:
            continue
        tail = re.sub(r"<[^>]+>", " ", match.group(3))
        tail = re.sub(r"\s+", " ", html_module_unescape(tail)).strip()
        title = " ".join(tail.split()[:6])
        if _TITLE_RE.search(title):
            add(heading, title, "team card")

    return people


def html_module_unescape(text: str) -> str:
    import html as _html
    return _html.unescape(text)


async def _person_email(first: str, last: str, domain: str) -> dict:
    """Try the most likely patterns for one person and stop at the first verified mailbox."""
    f = first.lower()
    l = last.lower()
    tried: List[dict] = []
    for pattern_name, template in PERSON_PATTERNS[:MAX_PATTERNS]:
        local = template.format(first=f, last=l, f=f[:1], l=l[:1])
        address = f"{local}@{domain}"
        verdict = await enrichment.verify_email(address, use_smtp=True)
        status = verdict.get("result") or verdict.get("status")
        tried.append({"email": address, "status": status, "reason": verdict.get("reason")})
        if status in ("valid", "catch_all"):
            return {"email": address, "status": status, "score": verdict.get("score"),
                    "pattern": pattern_name, "tried": tried}
    return {"email": None, "status": "not found", "score": None, "pattern": None, "tried": tried}


async def find_people(domain: str, html_pages: List[str]) -> List[dict]:
    """Harvest names across the pages we read, then verify each person's address."""
    harvested: List[dict] = []
    seen: set[str] = set()
    for html in html_pages:
        for person in _extract_people(html):
            if person["name"].lower() in seen:
                continue
            seen.add(person["name"].lower())
            harvested.append(person)
        if len(harvested) >= MAX_PEOPLE:
            break

    harvested = harvested[:MAX_PEOPLE]
    if not harvested:
        return []

    semaphore = asyncio.Semaphore(6)

    async def worker(person: dict) -> dict:
        async with semaphore:
            found = await _person_email(person["first"], person["last"], domain)
            return {**person, **found}

    return await asyncio.gather(*(worker(p) for p in harvested))


async def _discover_team_paths(client: httpx.AsyncClient, base: str, already: List[str]) -> List[str]:
    """Pull team/about URLs out of the site's sitemap.

    Fixed paths miss sites that call the page /our-team, /company/leadership or /meet-us. The sitemap
    knows what actually exists, and it costs one request.
    """
    found: List[str] = []
    for candidate in ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml"):
        body = await _fetch(client, base + candidate)
        if "<" not in body:
            continue
        urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", body)
        # a sitemap index points at child sitemaps; follow the page-ish ones
        children = [u for u in urls if u.endswith(".xml")][:4]
        for child in children:
            body += await _fetch(client, child)
        urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", body)
        for url in urls:
            if not url.startswith(base):
                continue
            path = "/" + url[len(base):].lstrip("/")
            if path in already or path in found:
                continue
            if any(hint in path.lower() for hint in TEAM_HINTS) and len(found) < 6:
                found.append(path)
        if found:
            break
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
    pages_html: List[str] = []
    async with httpx.AsyncClient(headers={"User-Agent": UA, "Accept": "text/html,*/*"}) as client:
        paths = list(PATHS)
        try:
            paths += await _discover_team_paths(client, base, paths)
        except Exception:
            pass

        for path in paths:
            if not await _robots_allows(client, base, path):
                notes.append(f"robots.txt disallows {path}")
                continue
            html = await _fetch(client, base + path)
            if not html:
                continue
            pages.append(path)
            pages_html.append(html)
            for address, how in _extract(html, domain).items():
                published.setdefault(address, f"{how} on {path}")

    people: List[dict] = []
    if smtp:
        people = await find_people(domain, list(pages_html))

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

    return {"domain": domain, "mx": mx_hosts, "pagesRead": pages, "notes": notes,
            "people": people, "emails": emails}

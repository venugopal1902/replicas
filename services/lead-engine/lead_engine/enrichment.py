"""Contact enrichment: turn a name + company (or a LinkedIn URL) into a verified
work email, and pull a direct email and phone from provider APIs when they have one.

Resolution order used by ``enrich_person``:

1. **Providers first.** If any API key is present, query Apollo, then Hunter, then
   People Data Labs. The first provider that returns a work email wins; the direct
   email and phone are taken from that same provider. A failing or keyless provider
   returns nothing and is recorded as a note — it never aborts the lookup.
2. **Pattern guessing.** With no provider result but a known domain, generate the
   common corporate local-part patterns (ordered by real-world hit rate) and verify
   each candidate. Stop at the first SMTP-confirmed ``valid``; otherwise keep the
   best non-invalid result (``catch_all`` beats ``risky``).
3. **Unavailable.** If nothing resolves, the work email is ``None`` with status
   ``unavailable``.

Hard rule: a direct email and a phone number are *only* ever copied from a provider
response. This module never fabricates either one. When no provider supplied one,
``directEmail`` is ``None`` (status ``unavailable``) and ``phone`` is ``None``
(source ``None``). A work email is likewise never reported as ``valid`` unless an
SMTP RCPT probe actually confirmed it.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import dns.asyncresolver
import dns.exception
import httpx

from .config import settings

# ---------------------------------------------------------------------------
# Caches. Populated lazily on first use — nothing runs at import time.
# ---------------------------------------------------------------------------

_mx_cache: Dict[str, List[str]] = {}
_domain_cache: Dict[str, Optional[str]] = {}

_PROVIDER_TIMEOUT = 15.0

# Real-world hit-rate order. ``multi_token_last`` flags patterns whose local part
# would be ambiguous for a multi-word surname, so they use only the first token.
_PATTERNS: List[tuple] = [
    ("first.last", 0.44, "{first}.{last}"),
    ("firstlast", 0.12, "{first}{last}"),
    ("first", 0.10, "{first}"),
    ("f.last", 0.08, "{f}.{last}"),
    ("first_last", 0.06, "{first}_{last}"),
    ("last.first", 0.05, "{last}.{first}"),
    ("flast", 0.05, "{f}{last}"),
    ("first.l", 0.03, "{first}.{l}"),
    ("last", 0.03, "{last}"),
    ("lastfirst", 0.02, "{last}{first}"),
    ("firstl", 0.02, "{first}{l}"),
]

_ROLE_LOCALS = {"info", "sales", "admin", "support", "noreply", "contact"}

_LEGAL_SUFFIXES = {"ltd", "inc", "gmbh", "llc", "plc", "co", "limited"}

_DOMAIN_TLDS = ("com", "io", "co", "ai", "app", "co.uk", "net")

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _ascii_fold(text: str) -> str:
    """Lowercase, strip accents (NFKD) and drop everything that isn't a-z."""
    normalised = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(ch for ch in normalised if not unicodedata.combining(ch))
    return re.sub(r"[^a-z]", "", stripped.lower())


def _looks_like_domain(value: str) -> bool:
    value = (value or "").strip().lower().rstrip(".")
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9.-]*\.[a-z]{2,}", value))


def score_for(status: str) -> str:
    """Map a verification status to the letter grade the API contract exposes."""
    return {
        "verified": "A+",
        "valid": "A+",
        "catch_all": "A",
        "risky": "B",
        "unknown": "C",
        "invalid": "",
        "unavailable": "",
    }.get(status or "", "")


def _empty_result(domain: Optional[str], notes: List[str]) -> Dict[str, Any]:
    return {
        "workEmail": None,
        "workEmailStatus": "unavailable",
        "workEmailScore": None,
        "workEmailPattern": None,
        "directEmail": None,
        "directEmailStatus": "unavailable",
        "directEmailScore": None,
        "phone": None,
        "phoneSource": None,
        "domain": domain,
        "sources": [],
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# 1. LinkedIn URLs
# ---------------------------------------------------------------------------


def parse_linkedin_url(url: str) -> dict:
    """Parse a LinkedIn profile URL into ``{is_valid, handle, name_guess}``.

    Accepts ``linkedin.com/in/<slug>`` with or without a scheme, a trailing slash,
    a query string, or a locale segment (``/in/uk/...``). ``name_guess`` drops a
    trailing hex/id segment and title-cases the remaining hyphen or underscore
    separated tokens, so ``maya-okafor-1a2b`` becomes ``Maya Okafor``.
    """
    raw = (url or "").strip()
    if not raw:
        return {"is_valid": False, "handle": None, "name_guess": None}

    candidate = raw if "://" in raw else "https://" + raw
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return {"is_valid": False, "handle": None, "name_guess": None}

    host = (parsed.netloc or "").lower()
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        return {"is_valid": False, "handle": None, "name_guess": None}
    if parsed.scheme not in ("http", "https"):
        return {"is_valid": False, "handle": None, "name_guess": None}

    segments = [s for s in parsed.path.split("/") if s]
    try:
        in_index = segments.index("in")
    except ValueError:
        return {"is_valid": False, "handle": None, "name_guess": None}

    after = segments[in_index + 1:]
    if not after:
        return {"is_valid": False, "handle": None, "name_guess": None}

    # Locale prefixes (/in/uk/...) sit between "in" and the slug.
    slug = after[-1]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", slug):
        return {"is_valid": False, "handle": None, "name_guess": None}

    tokens = re.split(r"[-_]+", slug)
    while len(tokens) > 1 and re.fullmatch(r"[0-9a-f]+", tokens[-1]):
        tokens.pop()
    name_tokens = [t for t in tokens if t and not t.isdigit()]
    name_guess = " ".join(t.capitalize() for t in name_tokens) or None

    return {"is_valid": True, "handle": slug, "name_guess": name_guess}


# ---------------------------------------------------------------------------
# 2. Email patterns
# ---------------------------------------------------------------------------


def email_patterns(first: str, last: str, domain: str) -> List[dict]:
    """Corporate address candidates for a person, best hit-rate first.

    Returns ``[{email, pattern, weight}]``. Names are lowercased with accents
    stripped; a multi-word surname contributes only its first token. Patterns that
    need a missing name part are skipped, and nothing with an empty local part or a
    doubled dot is ever emitted.
    """
    first_norm = _ascii_fold(first)
    last_tokens = [t for t in re.split(r"\s+", (last or "").strip()) if t]
    last_norm = _ascii_fold(last_tokens[0]) if last_tokens else ""
    domain_norm = (domain or "").strip().lower().lstrip("@")

    if not domain_norm or not _looks_like_domain(domain_norm):
        return []

    values = {
        "first": first_norm,
        "last": last_norm,
        "f": first_norm[:1],
        "l": last_norm[:1],
    }

    results: List[dict] = []
    seen = set()
    for name, weight, template in _PATTERNS:
        placeholders = set(re.findall(r"{(\w+)}", template))
        if any(not values.get(p) for p in placeholders):
            continue
        local = template.format(**values)
        if not local or ".." in local or local.startswith(".") or local.endswith("."):
            continue
        email = f"{local}@{domain_norm}"
        if email in seen:
            continue
        seen.add(email)
        results.append({"email": email, "pattern": name, "weight": weight})
    return results


# ---------------------------------------------------------------------------
# 3 & 4. DNS: MX lookup and domain resolution
# ---------------------------------------------------------------------------


async def mx_lookup(domain: str) -> List[str]:
    """MX hostnames for ``domain``, best preference first. Cached; ``[]`` on any error.

    A null MX (a single ``.`` exchange, per RFC 7505) means the domain explicitly
    accepts no mail, so it is treated as having no records.
    """
    domain = (domain or "").strip().lower().rstrip(".")
    if not domain:
        return []
    if domain in _mx_cache:
        return list(_mx_cache[domain])

    hosts: List[str] = []
    try:
        resolver = dns.asyncresolver.Resolver()
        resolver.lifetime = 4.0
        resolver.timeout = 4.0
        answers = await resolver.resolve(domain, "MX")
        ordered = sorted(answers, key=lambda record: record.preference)
        for record in ordered:
            host = str(record.exchange).rstrip(".").lower()
            if host and host != ".":
                hosts.append(host)
    except Exception:
        hosts = []

    _mx_cache[domain] = hosts
    return list(hosts)


def _company_slug(company_name: str) -> str:
    words = re.split(r"\s+", (company_name or "").strip().lower())
    while words and words[-1].strip(".") in _LEGAL_SUFFIXES:
        words.pop()
    return re.sub(r"[^a-z0-9]", "", " ".join(words))


async def resolve_domain(company_name: str, explicit: Optional[str] = None) -> Optional[str]:
    """The mail domain for a company.

    An explicit value that already looks like a domain is returned normalised.
    Otherwise the company name is slugified (lowercased, legal suffixes such as
    ltd/inc/gmbh/llc/plc/co/limited removed) and ``<slug>.<tld>`` is tried for
    com, io, co, ai, app, co.uk, net — the first candidate with an MX record wins.
    Results are cached; ``None`` when nothing resolves.
    """
    if explicit and _looks_like_domain(explicit):
        return explicit.strip().lower().rstrip(".")

    slug = _company_slug(company_name)
    if not slug:
        return None
    if slug in _domain_cache:
        return _domain_cache[slug]

    resolved: Optional[str] = None
    for tld in _DOMAIN_TLDS:
        candidate = f"{slug}.{tld}"
        if await mx_lookup(candidate):
            resolved = candidate
            break

    _domain_cache[slug] = resolved
    return resolved


# ---------------------------------------------------------------------------
# 5. Verification
# ---------------------------------------------------------------------------


def _syntax_ok(email: str) -> bool:
    if email.count("@") != 1:
        return False
    local, _, domain = email.partition("@")
    if not local or not domain or "." not in domain:
        return False
    if ".." in email or local.startswith(".") or local.endswith("."):
        return False
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9._+-]*", local)) and bool(
        re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", domain)
    )


def _verdict(email: str, result: str, mx_host: Optional[str], reason: str,
             score: Optional[str] = None) -> dict:
    return {
        "email": email,
        "result": result,
        "score": score_for(result) if score is None else score,
        "mxHost": mx_host,
        "reason": reason,
    }


async def _smtp_probe(email: str, mx_hosts: List[str]) -> Optional[dict]:
    """RCPT TO probe against the best MX. ``None`` when the connection fails outright
    (refused, timed out, unreachable) so the caller can fall back to MX-only."""
    timeout = settings.SMTP_TIMEOUT
    for host in mx_hosts:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, 25), timeout
            )
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
            continue
        try:
            code = await _smtp_converse(reader, writer, email, timeout)
        except (asyncio.TimeoutError, ConnectionError, OSError):
            code = None
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        if code is None:
            continue
        if code in (250, 251):
            return _verdict(email, "valid", host, f"smtp rcpt {code}")
        if code in (550, 551, 553):
            return _verdict(email, "invalid", host, f"smtp rcpt {code}")
        if code == 252 or 200 <= code < 300:
            return _verdict(email, "catch_all", host, f"smtp rcpt {code}")
        if code in (450, 451, 452):
            return _verdict(email, "risky", host, f"smtp rcpt {code}")
        return _verdict(email, "unknown", host, f"smtp rcpt {code}")
    return None


async def _smtp_converse(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                         email: str, timeout: float) -> Optional[int]:
    async def read_reply() -> int:
        code = 0
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout)
            if not line:
                break
            text = line.decode("utf-8", "replace")
            code = int(text[:3])
            if len(text) < 4 or text[3] != "-":
                break
        return code

    if await read_reply() >= 400:
        return None

    async def send(command: str) -> int:
        writer.write((command + "\r\n").encode("ascii", "replace"))
        await writer.drain()
        return await read_reply()

    domain = email.partition("@")[2]
    if await send(f"EHLO {domain}") >= 400:
        await send(f"HELO {domain}")
    await send(f"MAIL FROM:<{settings.SMTP_FROM}>")
    code = await send(f"RCPT TO:<{email}>")
    try:
        await send("QUIT")
    except Exception:
        pass
    return code


async def verify_email(email: str, use_smtp: Optional[bool] = None) -> dict:
    """Classify an address as ``valid | invalid | risky | catch_all | unknown``.

    Syntax first, then role-account detection, then MX existence. An SMTP RCPT probe
    on port 25 only runs when ``settings.SMTP_PROBE`` is enabled and ``use_smtp`` is
    not explicitly ``False``; connection failures fall back to the MX-only verdict.
    ``valid`` is only ever returned off a confirming SMTP response.
    """
    email = (email or "").strip().lower()

    if not _syntax_ok(email):
        return _verdict(email, "invalid", None, "malformed address")

    local, _, domain = email.partition("@")
    if local in _ROLE_LOCALS:
        return _verdict(email, "risky", None, "role account", score="B")

    mx_hosts = await mx_lookup(domain)
    if not mx_hosts:
        return _verdict(email, "invalid", None, "no mx record")

    probe = settings.SMTP_PROBE and use_smtp is not False
    if probe:
        probed = await _smtp_probe(email, mx_hosts)
        if probed is not None:
            return probed

    return _verdict(email, "risky", mx_hosts[0], "mx present, smtp not probed")


# ---------------------------------------------------------------------------
# 7. Provider adapters. Each is total: any error or missing key yields {}.
# ---------------------------------------------------------------------------


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


async def _hunter(key: str, first: str, last: str, domain: str) -> dict:
    if not key or not domain:
        return {}
    try:
        async with httpx.AsyncClient(timeout=_PROVIDER_TIMEOUT) as client:
            response = await client.get(
                "https://api.hunter.io/v2/email-finder",
                params={
                    "domain": domain,
                    "first_name": first,
                    "last_name": last,
                    "api_key": key,
                },
            )
            response.raise_for_status()
            data = _as_dict(_as_dict(response.json()).get("data"))
    except Exception:
        return {}

    email = data.get("email")
    if not email:
        return {}
    return {
        "workEmail": email,
        "workEmailScore": data.get("score"),
        "directEmail": None,
        "phone": None,
        "source": "hunter",
    }


async def _apollo(key: str, first: str, last: str, company: str, domain: str) -> dict:
    if not key:
        return {}
    try:
        async with httpx.AsyncClient(timeout=_PROVIDER_TIMEOUT) as client:
            response = await client.post(
                "https://api.apollo.io/v1/people/match",
                headers={"x-api-key": key},
                json={
                    "first_name": first,
                    "last_name": last,
                    "organization_name": company,
                    "domain": domain,
                    "reveal_personal_emails": True,
                },
            )
            response.raise_for_status()
            person = _as_dict(_as_dict(response.json()).get("person"))
    except Exception:
        return {}

    if not person:
        return {}
    personal = person.get("personal_emails") or []
    phones = person.get("phone_numbers") or []
    phone = None
    if phones and isinstance(phones[0], dict):
        phone = phones[0].get("sanitized_number")
    return {
        "workEmail": person.get("email"),
        "directEmail": personal[0] if personal else None,
        "phone": phone,
        "source": "apollo",
    }


async def _pdl(key: str, first: str, last: str, company: str) -> dict:
    if not key:
        return {}
    try:
        async with httpx.AsyncClient(timeout=_PROVIDER_TIMEOUT) as client:
            response = await client.get(
                "https://api.peopledatalabs.com/v5/person/enrich",
                params={"first_name": first, "last_name": last, "company": company},
                headers={"X-Api-Key": key},
            )
            response.raise_for_status()
            data = _as_dict(response.json())
    except Exception:
        return {}

    person = _as_dict(data.get("data")) or data
    personal = person.get("personal_emails") or []
    phones = person.get("phone_numbers") or []
    return {
        "workEmail": person.get("work_email"),
        "directEmail": personal[0] if personal else None,
        "phone": phones[0] if phones else None,
        "source": "pdl",
    }


_ADAPTERS = (
    ("apollo", lambda key, first, last, company, domain: _apollo(key, first, last, company, domain)),
    ("hunter", lambda key, first, last, company, domain: _hunter(key, first, last, domain)),
    ("pdl", lambda key, first, last, company, domain: _pdl(key, first, last, company)),
)


# ---------------------------------------------------------------------------
# 6. Top-level enrichment
# ---------------------------------------------------------------------------


def _apply_provider(result: Dict[str, Any], found: dict) -> None:
    """Copy a provider payload into the result. Direct email and phone only move
    across when the provider actually returned them — never invented."""
    source = found.get("source")
    result["sources"].append(source)
    result["workEmail"] = found.get("workEmail")
    result["workEmailStatus"] = "unknown"
    result["workEmailScore"] = score_for("unknown")
    result["workEmailPattern"] = None

    direct = found.get("directEmail")
    if direct:
        result["directEmail"] = direct
        result["directEmailStatus"] = "unknown"
        result["directEmailScore"] = score_for("unknown")
        result["sources"].append(f"{source}:direct")

    phone = found.get("phone")
    if phone:
        result["phone"] = phone
        result["phoneSource"] = source


async def _from_patterns(domain: str, first: str, last: str,
                          use_smtp: bool) -> Optional[dict]:
    """Verify generated candidates. A confirmed ``valid`` wins immediately; otherwise
    the best non-invalid result is kept, with ``catch_all`` preferred over ``risky``."""
    best: Optional[dict] = None
    rank = {"catch_all": 2, "risky": 1}
    for candidate in email_patterns(first, last, domain):
        verdict = await verify_email(candidate["email"], use_smtp=use_smtp)
        verdict["pattern"] = candidate["pattern"]
        if verdict["result"] == "valid":
            return verdict
        if verdict["result"] in rank and (best is None or rank[verdict["result"]] > rank.get(best["result"], 0)):
            best = verdict
    return best


async def enrich_person(
    first_name: str,
    last_name: str,
    company_name: Optional[str] = None,
    domain: Optional[str] = None,
    linkedin_url: Optional[str] = None,
    providers: Optional[dict] = None,
) -> dict:
    """Resolve a person's work email, direct email and phone.

    See the module docstring for the resolution order. ``providers`` is
    ``{"hunter": key, "apollo": key, "pdl": key}``; pass ``None`` to read the keys
    from settings. Direct emails and phone numbers come only from provider data.
    """
    notes: List[str] = []

    if linkedin_url:
        parsed = parse_linkedin_url(linkedin_url)
        if parsed["is_valid"]:
            notes.append(f"linkedin handle: {parsed['handle']}")
            if not (first_name or last_name) and parsed["name_guess"]:
                parts = parsed["name_guess"].split(" ", 1)
                first_name = parts[0]
                last_name = parts[1] if len(parts) > 1 else ""
        else:
            notes.append("linkedin url not parseable")

    first_name = first_name or ""
    last_name = last_name or ""

    if not domain and company_name:
        try:
            domain = await resolve_domain(company_name)
        except Exception:
            domain = None
            notes.append("domain resolution failed")
        if domain is None:
            notes.append(f"no domain resolved for {company_name}")

    result = _empty_result(domain, notes)

    keys = providers if providers is not None else {
        "hunter": settings.HUNTER_API_KEY,
        "apollo": settings.APOLLO_API_KEY,
        "pdl": settings.PDL_API_KEY,
    }

    for provider_name, adapter in _ADAPTERS:
        key = (keys or {}).get(provider_name) or ""
        if not key:
            continue
        try:
            found = await adapter(key, first_name, last_name, company_name or "", domain or "")
        except Exception:
            found = {}
        if not found or not found.get("workEmail"):
            notes.append(f"{provider_name}: no result")
            continue
        _apply_provider(result, found)
        notes.append(f"work email from {provider_name}")
        return result

    if domain:
        try:
            best = await _from_patterns(domain, first_name, last_name, use_smtp=settings.SMTP_PROBE)
        except Exception:
            best = None
            notes.append("pattern verification failed")
        if best is not None:
            result["workEmail"] = best["email"]
            result["workEmailStatus"] = best["result"]
            result["workEmailScore"] = best["score"] or None
            result["workEmailPattern"] = best.get("pattern")
            result["sources"].append("pattern")
            notes.append(f"pattern {best.get('pattern')}: {best['result']}")
            return result

    return result

"""Find a company's public email addresses for free.

    uv run python scripts/find_company_emails.py stripe.com
    uv run python scripts/find_company_emails.py figma.com --smtp --json

What this does, and why it is free:

1. Fetches the company's own public pages (homepage, contact, about, team, press, careers).
2. Extracts every address they publish: mailto: links, plain text, and the usual obfuscations
   ("name [at] domain.com", "name (at) domain.com").
3. Adds the standard role inboxes for the domain (info@, sales@, support@, hello@ ...).
4. Checks DNS MX for the domain, so you know mail can be delivered at all.
5. Optionally SMTP-probes each candidate to see whether the mailbox exists. Off by default:
   many networks block outbound port 25, and some servers treat probing as abuse.

This finds addresses a company publishes or would obviously own. It cannot invent a specific
person's private address: only a paid data provider has those. Nothing here logs in, bypasses a
paywall or scrapes a site that forbids it - it reads public pages and respects robots.txt.

Respect the law where your recipients are: unsolicited mail has real rules (CAN-SPAM, GDPR, PECR).
Role inboxes are usually fine to contact; individuals need a lawful basis.
"""
from __future__ import annotations

import argparse
import json
import re
import smtplib
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Set

try:
    import dns.resolver
except Exception:  # pragma: no cover
    dns = None  # type: ignore

UA = "Mozilla/5.0 (compatible; lead-engine-company-emails/1.0)"
PATHS = ["/", "/contact", "/contact-us", "/about", "/about-us", "/team", "/people", "/press", "/careers"]
ROLES = ["info", "hello", "contact", "sales", "support", "careers", "jobs", "press", "team", "admin"]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
OBFUSCATED_RE = re.compile(
    r"([A-Za-z0-9._%+\-]+)\s*(?:\[at\]|\(at\)|\sat\s|&#64;|&commat;)\s*"
    r"([A-Za-z0-9.\-]+\s*(?:\[dot\]|\(dot\)|\.)\s*[A-Za-z]{2,})",
    re.IGNORECASE,
)


@dataclass
class Finding:
    email: str
    source: str
    verified: str = "unknown"          # valid | invalid | catch_all | unverifiable
    detail: str = ""


@dataclass
class Report:
    domain: str
    mx_hosts: List[str] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)
    pages_read: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def fetch(url: str, timeout: float = 12.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status != 200:
                return ""
            raw = r.read(400_000)
            return raw.decode("utf-8", "ignore")
    except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout, OSError):
        return ""


def allowed_by_robots(base: str, path: str) -> bool:
    """Minimal robots check: refuse a path only when a Disallow matches it exactly."""
    robots = fetch(urllib.parse.urljoin(base, "/robots.txt"), timeout=8)
    if not robots:
        return True
    for line in robots.splitlines():
        if line.lower().startswith("disallow:"):
            rule = line.split(":", 1)[1].strip()
            if rule and rule != "/" and path.startswith(rule):
                return False
            if rule == "/":
                return False
    return True


def extract(html: str, domain: str) -> Dict[str, str]:
    """Return {email: how_it_was_found}. Only addresses on the target domain are kept."""
    found: Dict[str, str] = {}

    for match in re.finditer(r'href="mailto:([^"?]+)', html, re.I):
        addr = urllib.parse.unquote(match.group(1)).strip().lower()
        if addr.endswith("@" + domain):
            found.setdefault(addr, "mailto link")

    for match in EMAIL_RE.finditer(html):
        addr = match.group(0).lower().strip(".,;:")
        if addr.endswith("@" + domain) and not addr.startswith(("example@", "user@")):
            found.setdefault(addr, "page text")

    for match in OBFUSCATED_RE.finditer(html):
        local = match.group(1).lower()
        host = re.sub(r"\s*(?:\[dot\]|\(dot\))\s*", ".", match.group(2), flags=re.I).strip()
        addr = f"{local}@{host}".replace(" ", "")
        if addr.endswith("@" + domain):
            found.setdefault(addr, "obfuscated text")

    return found


def mx_lookup(domain: str) -> List[str]:
    if dns is None:
        return []
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=8)
        return sorted(str(r.exchange).rstrip(".") for r in answers)
    except Exception:
        return []


def smtp_verify(domain: str, email: str, mx_hosts: List[str], timeout: float = 8.0) -> tuple[str, str]:
    """RCPT probe. Returns (result, detail). Many networks block port 25 - that is reported, not hidden."""
    if not mx_hosts:
        return "unverifiable", "no MX"
    try:
        with smtplib.SMTP(mx_hosts[0], 25, timeout=timeout) as smtp:
            smtp.ehlo("verify.local")
            smtp.mail("verify@verify.local")
            code, message = smtp.rcpt(email)
            text = message.decode("utf-8", "ignore") if isinstance(message, bytes) else str(message)
            if code in (250, 251):
                return "valid", text[:80]
            if code in (550, 551, 553):
                return "invalid", text[:80]
            return "catch_all", text[:80]
    except (smtplib.SMTPException, OSError) as exc:
        return "unverifiable", str(exc)[:80]


def run(domain: str, do_smtp: bool, extra_roles: List[str]) -> Report:
    domain = domain.lower().strip().removeprefix("https://").removeprefix("http://").strip("/")
    domain = domain.split("/")[0]
    report = Report(domain=domain)
    base = f"https://{domain}"

    report.mx_hosts = mx_lookup(domain)
    if not report.mx_hosts:
        report.notes.append("no MX records: this domain cannot receive mail")
        return report

    published: Dict[str, str] = {}
    for path in PATHS:
        if not allowed_by_robots(base, path):
            report.notes.append(f"robots.txt disallows {path}")
            continue
        html = fetch(urllib.parse.urljoin(base, path))
        if not html:
            continue
        report.pages_read.append(path)
        for addr, how in extract(html, domain).items():
            published.setdefault(addr, f"{how} on {path}")

    for addr, how in sorted(published.items()):
        report.findings.append(Finding(addr, how))

    # Role inboxes: legitimate to include, clearly labelled as constructed rather than published.
    existing = {f.email for f in report.findings}
    for role in ROLES + [r for r in extra_roles if r]:
        addr = f"{role}@{domain}"
        if addr not in existing:
            report.findings.append(Finding(addr, "role address (constructed)"))
            existing.add(addr)

    if do_smtp:
        for finding in report.findings:
            finding.verified, finding.detail = smtp_verify(domain, finding.email, report.mx_hosts)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="Find a company's public emails for free")
    ap.add_argument("domain")
    ap.add_argument("--smtp", action="store_true", help="RCPT-probe each candidate (needs outbound port 25)")
    ap.add_argument("--role", action="append", default=[], help="extra role local-part, repeatable")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    report = run(args.domain, args.smtp, args.role)

    if args.json:
        print(json.dumps({
            "domain": report.domain,
            "mx": report.mx_hosts,
            "pagesRead": report.pages_read,
            "notes": report.notes,
            "emails": [
                {"email": f.email, "source": f.source, "verified": f.verified, "detail": f.detail}
                for f in report.findings
            ],
        }, indent=2))
        return 0

    print(f"\nDOMAIN  {report.domain}")
    print(f"MX      {', '.join(report.mx_hosts) or 'none'}")
    print(f"PAGES   {', '.join(report.pages_read) or 'none reachable'}")
    for note in report.notes:
        print(f"NOTE    {note}")

    published = [f for f in report.findings if "constructed" not in f.source]
    constructed = [f for f in report.findings if "constructed" in f.source]
    print(f"\nPUBLISHED BY THE COMPANY ({len(published)})")
    for f in published:
        extra = f"  [{f.verified}] {f.detail}" if args.smtp else ""
        print(f"  {f.email:38s} {f.source}{extra}")
    if not published:
        print("  none found on the pages we could read")

    print(f"\nROLE ADDRESSES, CONSTRUCTED ({len(constructed)})")
    for f in constructed:
        extra = f"  [{f.verified}] {f.detail}" if args.smtp else ""
        print(f"  {f.email:38s} {extra}")
    print("\nA constructed role address is a guess that the domain accepts mail; only --smtp proves a mailbox.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Test suite for lead_engine.enrichment.

Run with:
    uv run python tests/test_enrichment.py
"""

import asyncio
from pathlib import Path
import sys
import time

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from lead_engine import enrichment


class TestRunner:
    def __init__(self) -> None:
        self.passes = 0
        self.fails = 0
        self.results: list[tuple[str, bool, str]] = []

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.passes += 1
            self.results.append((name, True, detail))
            print(f"PASS: {name}")
        else:
            self.fails += 1
            self.results.append((name, False, detail))
            print(f"FAIL: {name} - {detail}")


async def test_parse_linkedin_url(runner: TestRunner) -> None:
    # Form 1: standard URL with trailing hex/id
    r1 = enrichment.parse_linkedin_url("https://www.linkedin.com/in/maya-okafor-1a2b")
    runner.check(
        "parse_linkedin_url: standard with hex id",
        r1["is_valid"] is True
        and r1["handle"] == "maya-okafor-1a2b"
        and r1["name_guess"] == "Maya Okafor",
        f"got {r1}",
    )

    # Form 2: scheme-less with trailing slash and query param
    r2 = enrichment.parse_linkedin_url("linkedin.com/in/john-doe/?trk=public_profile")
    runner.check(
        "parse_linkedin_url: scheme-less with query",
        r2["is_valid"] is True
        and r2["handle"] == "john-doe"
        and r2["name_guess"] == "John Doe",
        f"got {r2}",
    )

    # Form 3: locale prefix (/in/uk/...) and underscore separator
    r3 = enrichment.parse_linkedin_url("https://uk.linkedin.com/in/uk/jane_smith_4f5e6d")
    runner.check(
        "parse_linkedin_url: locale prefix and underscore",
        r3["is_valid"] is True
        and r3["handle"] == "jane_smith_4f5e6d"
        and r3["name_guess"] == "Jane Smith",
        f"got {r3}",
    )

    # Form 4: invalid string (not a LinkedIn profile)
    r4 = enrichment.parse_linkedin_url("https://twitter.com/not-linkedin")
    runner.check(
        "parse_linkedin_url: invalid string rejected",
        r4["is_valid"] is False
        and r4["handle"] is None
        and r4["name_guess"] is None,
        f"got {r4}",
    )


async def test_email_patterns(runner: TestRunner) -> None:
    # 1. Ordering and weights
    pats = enrichment.email_patterns("John", "Doe", "example.com")
    expected_order = [
        ("first.last", 0.44, "john.doe@example.com"),
        ("firstlast", 0.12, "johndoe@example.com"),
        ("first", 0.10, "john@example.com"),
        ("f.last", 0.08, "j.doe@example.com"),
        ("first_last", 0.06, "john_doe@example.com"),
        ("last.first", 0.05, "doe.john@example.com"),
        ("flast", 0.05, "jdoe@example.com"),
        ("first.l", 0.03, "john.d@example.com"),
        ("last", 0.03, "doe@example.com"),
        ("lastfirst", 0.02, "doejohn@example.com"),
        ("firstl", 0.02, "johnd@example.com"),
    ]
    actual = [(p["pattern"], p["weight"], p["email"]) for p in pats]
    runner.check(
        "email_patterns: ordering, weights, and addresses",
        actual == expected_order,
        f"expected {expected_order}, got {actual}",
    )

    # 2. Accent case and multi-word last name
    pats_accent = enrichment.email_patterns("José", "Álvarez Martinez", "acme.org")
    top_email = pats_accent[0]["email"] if pats_accent else None
    runner.check(
        "email_patterns: accent folding and multi-word last name",
        top_email == "jose.alvarez@acme.org",
        f"got {top_email}",
    )

    # 3. No malformed email emitted (no empty local part, no doubled dot)
    pats_missing_first = enrichment.email_patterns("", "Smith", "example.com")
    all_valid_local = all(
        p["email"].split("@")[0]
        and ".." not in p["email"]
        and not p["email"].startswith(".")
        for p in pats_missing_first
    )
    runner.check(
        "email_patterns: no malformed email when first name is empty",
        len(pats_missing_first) > 0 and all_valid_local,
        f"got {pats_missing_first}",
    )

    pats_empty = enrichment.email_patterns("", "", "example.com")
    runner.check(
        "email_patterns: empty list when both names empty",
        pats_empty == [],
        f"got {pats_empty}",
    )


async def test_verify_email(runner: TestRunner) -> None:
    # 1. Malformed address expecting invalid
    res_malformed = await enrichment.verify_email("not-an-email")
    runner.check(
        "verify_email: malformed address yields invalid",
        res_malformed["result"] == "invalid" and res_malformed["score"] == "",
        f"got {res_malformed}",
    )

    # 2. Role address expecting risky (score B)
    res_role = await enrichment.verify_email("support@stripe.com")
    runner.check(
        "verify_email: role address yields risky with score B",
        res_role["result"] == "risky" and res_role["score"] == "B",
        f"got {res_role}",
    )


async def test_mx_lookup(runner: TestRunner) -> None:
    gmail_mx = await enrichment.mx_lookup("gmail.com")
    fake_mx = await enrichment.mx_lookup("this-domain-should-not-exist-xyz123.com")
    runner.check(
        "mx_lookup: gmail.com non-empty and non-existent domain empty",
        len(gmail_mx) > 0 and fake_mx == [],
        f"gmail_mx={gmail_mx}, fake_mx={fake_mx}",
    )


async def test_resolve_domain(runner: TestRunner) -> None:
    # Network call, allow up to 20s
    t0 = time.time()
    resolved = await enrichment.resolve_domain("Stripe")
    duration = time.time() - t0
    mx = await enrichment.mx_lookup(resolved) if resolved else []
    runner.check(
        "resolve_domain: 'Stripe' resolves to domain with MX within 20s",
        resolved == "stripe.com" and len(mx) > 0 and duration < 20.0,
        f"resolved={resolved}, mx={mx}, took {duration:.2f}s",
    )


async def test_enrich_person(runner: TestRunner) -> None:
    # 1. enrich_person('Maya', 'Okafor', domain='stripe.com')
    res = await enrichment.enrich_person("Maya", "Okafor", domain="stripe.com")
    required_keys = {
        "workEmail",
        "workEmailStatus",
        "workEmailScore",
        "workEmailPattern",
        "directEmail",
        "directEmailStatus",
        "directEmailScore",
        "phone",
        "phoneSource",
        "domain",
        "sources",
        "notes",
    }
    keys_present = required_keys.issubset(res.keys())
    status_valid = True
    if res.get("workEmail"):
        status_valid = res.get("workEmailStatus") != "invalid"

    runner.check(
        "enrich_person: Maya Okafor at stripe.com returns required keys and non-invalid status",
        keys_present and status_valid and res.get("workEmail") is not None,
        f"keys_present={keys_present}, res={res}",
    )

    # 2. enrich_person with no domain and no company returns workEmail None, status 'unavailable'
    res_none = await enrichment.enrich_person("Maya", "Okafor")
    runner.check(
        "enrich_person: no domain and no company returns workEmail None and unavailable",
        res_none["workEmail"] is None and res_none["workEmailStatus"] == "unavailable",
        f"got {res_none}",
    )

    # 3. Hard rule: with providers={} directEmail and phone are None
    res_hard_rule = await enrichment.enrich_person(
        "Maya", "Okafor", domain="stripe.com", providers={}
    )
    runner.check(
        "enrich_person: hard rule - directEmail and phone are None with providers={}",
        res_hard_rule["directEmail"] is None
        and res_hard_rule["directEmailStatus"] == "unavailable"
        and res_hard_rule["directEmailScore"] is None
        and res_hard_rule["phone"] is None
        and res_hard_rule["phoneSource"] is None,
        f"got {res_hard_rule}",
    )


async def main() -> None:
    runner = TestRunner()
    print("--- Running test_parse_linkedin_url ---")
    await test_parse_linkedin_url(runner)
    print("--- Running test_email_patterns ---")
    await test_email_patterns(runner)
    print("--- Running test_verify_email ---")
    await test_verify_email(runner)
    print("--- Running test_mx_lookup ---")
    await test_mx_lookup(runner)
    print("--- Running test_resolve_domain ---")
    await test_resolve_domain(runner)
    print("--- Running test_enrich_person ---")
    await test_enrich_person(runner)

    print("\n========================================")
    print(f"Summary: {runner.passes} PASSED, {runner.fails} FAILED")
    print("========================================")

    if runner.fails > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())

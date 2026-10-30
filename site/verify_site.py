"""Verify the published catalog site, not just the local services.

    python site/verify_site.py
    python site/verify_site.py --site https://venugopal1902.github.io/replicas

Checks, against the live deployment rather than local files:
- the index responds and links every service in the manifest
- every service page responds with real content
- every localhost port a page advertises matches the port the service actually owns
- no service in the manifest is missing from the site, and no page is orphaned

Exits non-zero on any failure so it can gate a deploy.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "replicas.json"
DEFAULT_SITE = "https://presto-torque-aqk3.here.now"

# live port map; keep in step with SPRINT.md
PORTS = {
    "blotato-engine": 8400, "lead-engine": 8500, "back-in-stock": 8600, "churn-buster": 8610,
    "bannerbear-engine": 8620, "zigpoll-engine": 8630, "mail-engine": 8640,
    "baremetrics-engine": 8650, "smile-engine": 8660, "geckoboard-engine": 8670,
}
MIN_BYTES = 2000


def fetch(url: str, timeout: float = 25.0):
    req = urllib.request.Request(url, headers={"User-Agent": "verify-site/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default=DEFAULT_SITE)
    ap.add_argument("--min-bytes", type=int, default=MIN_BYTES)
    args = ap.parse_args()
    base = args.site.rstrip("/")

    services = json.loads(MANIFEST.read_text(encoding="utf-8"))["services"]
    failures = []

    st, index = fetch(base + "/")
    print(f"index {st} {len(index)} bytes")
    if st != 200:
        failures.append(f"index returned {st}")

    linked = {
        link.split("/")[-1].replace(".html", "")
        for link in re.findall(r'href="([^"#]+)"', index)
        if "/services/" in link or link.startswith("services/")
    }

    print(f"\n{'':4s}{'slug':22s}{'http':>6s}{'bytes':>8s}  ports")
    for svc in services:
        slug = svc["slug"]
        st, body = fetch(f"{base}/services/{slug}.html")
        ports = sorted(set(re.findall(r"127\.0\.0\.1:(\d+)", body)))
        ok = st == 200 and len(body) > args.min_bytes
        print(f"{'ok ' if ok else 'BAD'} {slug:22s}{st:6d}{len(body):8d}  {','.join(ports) or '-'}")
        if not ok:
            failures.append(f"{slug}: status={st} bytes={len(body)}")

        if slug in PORTS:
            wrong = [p for p in ports if int(p) != PORTS[slug]]
            if wrong:
                failures.append(f"{slug}: page advertises ports {wrong}, service owns {PORTS[slug]}")

        if slug not in linked:
            failures.append(f"{slug}: not linked from the index")

    orphans = linked - {s["slug"] for s in services}
    if orphans:
        failures.append(f"index links pages with no manifest entry: {sorted(orphans)}")

    print()
    if failures:
        print(f"FAILED {len(failures)} check(s):")
        for f in failures:
            print("  -", f)
        return 1
    print(f"OK  {len(services)} service pages reachable, ports consistent, index links all of them")
    return 0


if __name__ == "__main__":
    sys.exit(main())

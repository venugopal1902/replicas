"""Verify every replica in this repository from one command.

    python verify_all.py              # start what is down, run every suite, stop what it started
    python verify_all.py --keep       # leave started services running
    python verify_all.py --only smile-engine geckoboard-engine
    python verify_all.py --skip-start # only check services that are already up

Each service declares its port, how to start it, and how to run its checks. The script never
touches a service it did not start: if a port is already answering, it uses it and leaves it alone.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent


@dataclass
class Service:
    slug: str
    port: int
    checks: List[List[str]]                    # each: argv for one suite
    env: Dict[str, str] = field(default_factory=dict)
    needs: str = ""                            # note about required env
    starts: bool = True                        # run scripts/run_server.py


SERVICES: List[Service] = [
    Service("blotato-engine", 8400, [
        ["uv", "run", "python", "tests/test_engine.py"],
        ["uv", "run", "python", "tests/test_features.py"],
    ]),
    Service("lead-engine", 8500, [
        ["uv", "run", "python", "tests/test_lead_engine.py"],
    ]),
    Service("back-in-stock", 8600, [
        ["uv", "run", "python", "tests/test_back_in_stock.py"],
    ]),
    Service("churn-buster", 8610, [
        ["uv", "run", "python", "tests/test_churn_buster.py"],
    ]),
    Service("bannerbear-engine", 8620, [
        ["uv", "run", "--with", "pytest", "--with", "httpx", "--with", "pillow",
         "pytest", "tests/", "-q"],
    ], env={"BANNERBEAR_TEST_PORT": "8621"}),
    Service("zigpoll-engine", 8630, [
        ["uv", "run", "--with", "pytest", "--with", "httpx", "pytest", "tests/", "-q"],
    ], env={"ZIGPOLL_TEST_PORT": "8631"}, needs="ZIGPOLL_ADMIN_PASSWORD"),
    Service("mail-engine", 8640, [
        ["uv", "run", "python", "tests/test_units.py"],
        ["uv", "run", "python", "tests/test_mail_engine.py"],
    ]),
    Service("baremetrics-engine", 8650, [
        ["uv", "run", "python", "tests/test_baremetrics.py"],
    ], needs="ADMIN_PASSWORD"),
    Service("smile-engine", 8660, [
        ["uv", "run", "python", "tests/test_smile.py"],
    ], needs="ADMIN_PASSWORD"),
    Service("geckoboard-engine", 8670, [
        ["uv", "run", "--with", "pytest", "--with", "httpx", "--with", "pillow",
         "pytest", "tests/test_geckoboard.py", "-q"],
    ], env={"GECKOBOARD_TEST_PORT": "8641"},
       needs="GECKOBOARD_ADMIN_PASSWORD and GECKOBOARD_DATASET_API_KEY"),
]

# Local demo credentials. These are for a self-hosted demo box, not a deployment.
DEMO_ENV = {
    "mail-engine": {},
    "baremetrics-engine": {"ADMIN_PASSWORD": "baremetrics123"},
    "smile-engine": {"ADMIN_PASSWORD": "smile123"},
    "zigpoll-engine": {"ZIGPOLL_ADMIN_PASSWORD": "zigpoll123"},
    "geckoboard-engine": {"GECKOBOARD_ADMIN_PASSWORD": "geckoboard123",
                          "GECKOBOARD_DATASET_API_KEY": "local-demo-dataset-key"},
}

def parse_counts(text: str) -> Optional[tuple]:
    """Return (passed, total) for one suite's output.

    Order matters. "PASSED 52 FAILED 0" means 52 passed and 0 failed, so total is 52, not 0.
    A pytest line that only says "21 passed" has no failures, so total equals passed.
    """
    # explicit passed/failed pairs, in either spelling
    for pattern in (
        re.compile(r"PASSED\s+(\d+)\s+FAILED\s+(\d+)", re.I),
        re.compile(r"(\d+)\s+passed[,\s]+(\d+)\s+failed", re.I),
    ):
        m = pattern.search(text)
        if m:
            passed, failed = int(m.group(1)), int(m.group(2))
            return passed, passed + failed

    # "N/M checks passed" / "VERIFIED N/M"
    m = re.search(r"VERIFIED\s+(\d+)\s*/\s*(\d+)", text, re.I) or \
        re.search(r"(\d+)\s*/\s*(\d+)\s+(?:unit |live )?checks passed", text, re.I)
    if m:
        return int(m.group(1)), int(m.group(2))

    # pytest summary with only a passed count means zero failures
    m = re.search(r"(\d+)\s+passed", text, re.I)
    if m:
        n = int(m.group(1))
        failed = re.search(r"(\d+)\s+failed", text, re.I)
        return n, n + (int(failed.group(1)) if failed else 0)

    return None


def free_port() -> int:
    """Return a port the OS says is bindable right now.

    Test ports were hard-coded (8621, 8631, 8641) until Windows excluded a whole band of them and
    every suite in it failed with EACCES 10013 while nothing was actually listening. Asking the OS
    each run removes that failure mode entirely.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def health(port: int, timeout: float = 2.0) -> Optional[dict]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as r:
            if r.status == 200:
                return json.loads(r.read() or b"{}")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    return None


def wait_up(port: int, seconds: float = 40.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if health(port):
            return True
        time.sleep(0.7)
    return False


def run_checks(svc: Service) -> tuple:
    """Return (passed, total, combined_output)."""
    workdir = ROOT / "services" / svc.slug
    env = os.environ.copy()
    # any *_TEST_PORT placeholder gets a fresh OS-assigned port instead of a hard-coded one
    env.update({k: str(free_port()) if k.endswith("_TEST_PORT") else v for k, v in svc.env.items()})
    totals = []
    chunks = []
    for argv in svc.checks:
        try:
            res = subprocess.run(argv, cwd=workdir, env=env, capture_output=True,
                                 text=True, timeout=900)
            out = (res.stdout or "") + (res.stderr or "")
        except subprocess.TimeoutExpired:
            out = "TIMEOUT"
        except FileNotFoundError as exc:
            out = f"could not run {argv[0]}: {exc}"
        chunks.append(out)
        counts = parse_counts(out)
        if counts:
            totals.append(counts)
        elif "TIMEOUT" in out or "could not run" in out:
            totals.append((0, None))
    passed = sum(t[0] for t in totals)
    known = [t[1] for t in totals if t[1] is not None]
    total = sum(known) if known else None
    return passed, total, "\n".join(chunks)


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify every replica")
    ap.add_argument("--only", nargs="*", help="limit to these slugs")
    ap.add_argument("--keep", action="store_true", help="leave services this run started")
    ap.add_argument("--skip-start", action="store_true", help="do not start missing services")
    ap.add_argument("--verbose", action="store_true", help="print raw suite output on failure")
    args = ap.parse_args()

    selected = [s for s in SERVICES if not args.only or s.slug in args.only]
    if not selected:
        print("no matching services")
        return 2

    results = []
    started: List[tuple] = []

    for svc in selected:
        print(f"\n=== {svc.slug} (:{svc.port}) ===")
        proc = None
        if health(svc.port) is None:
            if args.skip_start:
                print("  not running and --skip-start given; skipping")
                results.append((svc.slug, None, None, "not running"))
                continue
            env = os.environ.copy()
            env.update(DEMO_ENV.get(svc.slug, {}))
            env.update(svc.env)
            print("  starting…")
            proc = subprocess.Popen(
                ["uv", "run", "python", "scripts/run_server.py"],
                cwd=ROOT / "services" / svc.slug, env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            started.append((svc, proc))
            if not wait_up(svc.port):
                print("  FAILED to become healthy")
                results.append((svc.slug, 0, None, "did not start"))
                continue
        else:
            print("  already running, leaving it alone")

        passed, total, out = run_checks(svc)
        ok = total is not None and passed == total
        detail = f"{passed}/{total}" if total is not None else f"{passed} passed (total unknown)"
        results.append((svc.slug, passed, total, "ok" if ok else "checks failed"))
        print(f"  {detail}  {'PASS' if ok else 'FAIL'}")
        if not ok and args.verbose:
            print("\n".join(out.splitlines()[-25:]))

    if not args.keep:
        for svc, proc in started:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                print(f"\nstopped {svc.slug} (started by this run)")

    print("\n" + "=" * 62)
    bad = [r for r in results if r[3] != "ok"]
    grand = sum(r[1] for r in results if isinstance(r[1], int))
    print(f"{len(results) - len(bad)}/{len(results)} services verified, {grand} checks passed")
    for slug, passed, total, note in results:
        mark = "PASS" if note == "ok" else "FAIL"
        shown = f"{passed}/{total}" if total is not None and passed is not None else "-"
        print(f"  {mark}  {slug:22s} {shown:>8s}  {note if note != 'ok' else ''}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())

"""Run every replica at once and keep them running.

    python serve_all.py            # start all ten, wait for health, then stay attached
    python serve_all.py --detach   # start them and exit (children may die with the parent shell)

The ten services were repeatedly found dead because they had been started by short-lived shells:
when the parent exited, nothing owned them. This script is the owner. Start it in a persistent
background terminal and the children live as long as it does; Ctrl-C stops all of them cleanly.

Status is re-checked every 60s, so a service that dies is reported instead of silently disappearing.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from verify_all import DEMO_ENV, SERVICES, health  # noqa: E402


def start(svc) -> subprocess.Popen | None:
    env = os.environ.copy()
    env.update(DEMO_ENV.get(svc.slug, {}))
    env.setdefault("PORT", str(svc.port))
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    try:
        return subprocess.Popen(
            ["uv", "run", "python", "scripts/run_server.py"],
            cwd=ROOT / "services" / svc.slug, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
    except FileNotFoundError as exc:
        print(f"  {svc.slug:22s} could not start: {exc}")
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--detach", action="store_true", help="start and exit without staying attached")
    ap.add_argument("--wait", type=float, default=45.0, help="seconds to wait for each health check")
    args = ap.parse_args()

    procs = []
    for svc in SERVICES:
        if health(svc.port):
            print(f"  {svc.slug:22s} :{svc.port} already up")
            continue
        proc = start(svc)
        deadline = time.time() + args.wait
        up = False
        while time.time() < deadline:
            if health(svc.port):
                up = True
                break
            time.sleep(0.5)
        print(f"  {svc.slug:22s} :{svc.port} {'up' if up else 'FAILED'}")
        if proc:
            procs.append((svc, proc))

    live = sum(1 for s in SERVICES if health(s.port))
    print(f"\n{live}/{len(SERVICES)} services healthy")
    if live != len(SERVICES):
        print("some services did not come up; check their logs before relying on them")

    if args.detach:
        print("detached: children are not owned by a supervisor and may exit with this shell")
        return 0 if live == len(SERVICES) else 1

    def _stop(*_):
        print("\nstopping all services…")
        for svc, proc in procs:
            if proc.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                   capture_output=True, check=False)
                else:
                    proc.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    print("supervising; Ctrl-C to stop everything. re-checking health every 60s\n")
    try:
        while True:
            time.sleep(60)
            down = [s.slug for s in SERVICES if not health(s.port)]
            if down:
                print(f"[{time.strftime('%H:%M:%S')}] DOWN: {', '.join(down)}", flush=True)
            else:
                print(f"[{time.strftime('%H:%M:%S')}] all {len(SERVICES)} healthy", flush=True)
    except KeyboardInterrupt:
        _stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

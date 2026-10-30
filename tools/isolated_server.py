"""Boot a throwaway service instance for one test run.

Why this exists: several suites assert against a *live* service over HTTP. Those runs write into
the live demo database, so they accumulate state across runs and eventually fail for reasons that
have nothing to do with the code. mail-engine hit exactly this - its e2e check passed when written,
then went permanently red once the shared mailbox burned its daily send quota.

`start_isolated` gives each run its own server, its own temporary database and its own port. The
suite then asserts against a known-empty world every time, so a green run means the engine is
correct rather than lucky.

Opt out with <PREFIX>_LIVE_SUITE=1 to point the suite back at the long-running instance.
"""
from __future__ import annotations

import atexit
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def _free_port() -> int:
    """Ask the OS for a bindable port.

    Hard-coded test ports are fragile on Windows: an excluded port range (Hyper-V, WSL, Docker) can
    make a specific number unbindable with EACCES 10013 even though nothing is listening on it.
    Port 0 sidesteps both that and any collision with a live service.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_healthy(base: str, proc: subprocess.Popen, seconds: float = 75.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            return False            # server died; no point waiting out the clock
        try:
            with urllib.request.urlopen(base + "/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(0.4)
    return False


def start_isolated(
    *,
    service_dir: Path,
    db_env: str,
    test_port: int = 0,
    db_url_prefix: str = "",
    live_port: int,
    admin_password: str,
    admin_email: str | None = None,
    opt_out_suffix: str = "_LIVE_SUITE",
    extra_env: dict[str, str] | None = None,
    quiet: bool = True,
) -> str:
    """Return the base URL to test against, starting a private server when needed."""
    service_dir = Path(service_dir).resolve()
    slug = service_dir.name.upper().replace("-", "_")
    if os.environ.get(slug + opt_out_suffix) == "1":
        base = f"http://127.0.0.1:{live_port}"
        print(f"[harness] live mode: testing the running instance at {base}")
        return base

    workdir = Path(tempfile.mkdtemp(prefix=f"{service_dir.name}-test-"))
    db_path = workdir / "test.db"

    # Resolve the port BEFORE building the env: the server reads PORT, and it must be the same
    # number the health probe waits on. Resolving it later sends PORT=0, the server picks its own
    # ephemeral port, and the probe then waits forever on the wrong one.
    if not test_port:
        test_port = _free_port()

    # Some engines take a bare path (SMILE_DB=/x/y.db); others take a full SQLAlchemy URL
    # (DATABASE_URL=sqlite+aiosqlite:////x/y.db). db_url_prefix covers the second shape.
    db_value = f"{db_url_prefix}{db_path}" if db_url_prefix else str(db_path)

    env = os.environ.copy()
    env.update({
        "PORT": str(test_port),
        db_env: db_value,
        "ADMIN_PASSWORD": admin_password,
    })
    if admin_email:
        env["ADMIN_EMAIL"] = admin_email
    if extra_env:
        env.update(extra_env)

    # 0 (the default) means "let the OS choose"; an explicit port is honoured for debugging.

    base = f"http://127.0.0.1:{test_port}"
    print(f"[harness] starting a private instance on {base} with a throwaway database")

    # Launch the server with this interpreter, not `uv run`. `uv run` is a wrapper: terminating it
    # leaves the real server holding the port, and the next run then fails to bind. sys.executable
    # is already the uv-provisioned interpreter, so the child is directly killable.
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    proc = subprocess.Popen(
        [sys.executable, "scripts/run_server.py"],
        cwd=str(service_dir), env=env,
        stdout=subprocess.DEVNULL if quiet else None,
        stderr=subprocess.DEVNULL if quiet else None,
        creationflags=creationflags,
    )

    def _kill_tree(p: subprocess.Popen) -> None:
        if p.poll() is not None:
            return
        if os.name == "nt":
            # a plain terminate() can leave grandchildren; /T kills the whole tree
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                           capture_output=True, check=False)
        else:
            p.terminate()
        try:
            p.wait(timeout=15)
        except subprocess.TimeoutExpired:
            p.kill()

    def _cleanup() -> None:
        _kill_tree(proc)
        # On Windows the database file stays locked briefly after the process dies, so a single
        # rmtree can fail with a sharing violation and silently leave the directory behind.
        for attempt in range(5):
            shutil.rmtree(workdir, ignore_errors=True)
            if not workdir.exists():
                return
            time.sleep(0.4 * (attempt + 1))

    atexit.register(_cleanup)

    if not _wait_healthy(base, proc):
        print(f"[harness] isolated instance did not become healthy on {base}", file=sys.stderr)
        _cleanup()
        raise SystemExit(2)
    return base

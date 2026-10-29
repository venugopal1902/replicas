"""Real HTTP acceptance checks for Zigpoll Engine."""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA_DIR = ROOT / "data-test"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / f"test-{secrets.token_hex(6)}.db"
ADMIN_PASSWORD = secrets.token_urlsafe(24)
TEST_VERSION = "acceptance-" + secrets.token_hex(6)
TEST_PORT = int(os.environ.get("ZIGPOLL_TEST_PORT", "8630"))


SURVEY_BODY = {
    "title": "Acceptance Survey",
    "description": "HTTP acceptance fixture",
    "status": "active",
    "questions": [
        {"id": "choice", "label": "Pick one", "type": "single_choice",
         "options": ["A", "B"], "required": True},
        {"id": "multi", "label": "Pick many", "type": "multi_choice",
         "options": ["Red", "Blue", "Green"], "required": True},
        {"id": "rating", "label": "Rate it", "type": "rating",
         "min_value": 1, "max_value": 5, "required": True},
        {"id": "comment", "label": "Comment", "type": "free_text", "required": False},
    ],
}
ANSWER_BODY = {
    "answers": {"choice": "A", "multi": ["Red", "Blue"], "rating": 4, "comment": "Works well."},
    "respondent_id": "acceptance-respondent",
}


class SafeState(dict):
    def __repr__(self):
        return "<acceptance state redacted>"


@pytest.fixture(scope="module")
def state():
    # The contract port is 8630, but an explicit ZIGPOLL_TEST_PORT override is honoured
    # (for example when a live instance already owns 8630). The real protection is the
    # occupancy check below: never touch or stop a service we did not start.
    probe = socket.socket()
    probe.settimeout(0.5)
    occupied = probe.connect_ex(("127.0.0.1", TEST_PORT)) == 0
    probe.close()
    if occupied:
        raise RuntimeError("Port 8630 is occupied; refusing to touch or stop an existing service")

    env = os.environ.copy()
    env.update({
        "ZIGPOLL_DATABASE_URL": f"sqlite+aiosqlite:///{DB_PATH.as_posix()}",
        "ZIGPOLL_DATA_DIR": str(DATA_DIR),
        "ZIGPOLL_ADMIN_EMAIL": "admin@zigpoll.test",
        "ZIGPOLL_ADMIN_PASSWORD": ADMIN_PASSWORD,
        "ZIGPOLL_VERSION": TEST_VERSION,
        "ZIGPOLL_PORT": str(TEST_PORT),
        "ZIGPOLL_DEBUG": "false",
    })
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "run_server.py")], cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{TEST_PORT}", timeout=12.0)
    responses = []
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Test-owned Zigpoll server exited with code {process.returncode}")
        try:
            response = client.get("/health")
            if response.status_code == 200:
                if response.json().get("version") != TEST_VERSION:
                    process.terminate()
                    process.wait(timeout=5)
                    raise RuntimeError("Port 8630 answered with a different service; refusing to test it")
                responses.append(("GET", "/health", response.status_code))
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    else:
        process.terminate()
        process.wait(timeout=5)
        raise RuntimeError("Test-owned Zigpoll server did not become healthy on port 8630")

    data = SafeState({"client": client, "process": process, "responses": responses,
                      "admin_token": None, "survey": None, "webhook_secret": None, "response_id": None})
    yield data
    client.close()
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    shutil.rmtree(DATA_DIR, ignore_errors=True)


def request(state, method: str, path: str, **kwargs):
    response = getattr(state["client"], method.lower())(path, **kwargs)
    state["responses"].append((method.upper(), path, response.status_code))
    return response


def passed(label: str) -> None:
    print(f"PASS | {label}")


def admin_headers(state):
    return {"Authorization": f"Bearer {state['admin_token']}"}


def test_01_health(state):
    response = request(state, "GET", "/health")
    assert response.status_code == 200
    assert response.json() == {"status": "online", "service": "Zigpoll Engine",
                               "version": TEST_VERSION, "port": TEST_PORT}
    passed("health returns online on the test-owned server")


def test_02_admin_login_200(state):
    response = request(state, "POST", "/api/auth/login", json={
        "email": "admin@zigpoll.test", "password": ADMIN_PASSWORD,
    })
    assert response.status_code == 200
    state["admin_token"] = response.json()["access_token"]
    assert response.json()["token_type"] == "bearer"
    passed("admin login returns 200")


def test_03_admin_routes_require_auth(state):
    response = request(state, "GET", "/api/admin/surveys")
    assert response.status_code == 401
    passed("admin survey list rejects unauthenticated request with 401")


def test_04_create_typed_survey_and_receive_public_id(state):
    response = request(state, "POST", "/api/admin/surveys", headers=admin_headers(state), json=SURVEY_BODY)
    assert response.status_code == 201
    survey = response.json()
    assert survey["public_id"].startswith("zp_") and survey["id"] != survey["public_id"]
    assert [q["type"] for q in survey["questions"]] == ["single_choice", "multi_choice", "rating", "free_text"]
    assert survey["webhook_secret"].startswith("zph_") and survey["webhook_secret_shown_once"] is True
    state["survey"] = survey
    state["webhook_secret"] = survey["webhook_secret"]
    passed("create survey preserves four distinct question types and separate public id")


def test_05_admin_list_omits_webhook_secret(state):
    response = request(state, "GET", "/api/admin/surveys", headers=admin_headers(state))
    assert response.status_code == 200
    item = next(row for row in response.json()["items"] if row["id"] == state["survey"]["id"])
    assert "webhook_secret" not in item
    passed("survey list omits the one-time webhook secret")


def test_06_update_survey(state):
    response = request(state, "PATCH", f"/api/admin/surveys/{state['survey']['id']}",
                       headers=admin_headers(state), json={"title": "Updated Acceptance Survey"})
    assert response.status_code == 200 and response.json()["title"] == "Updated Acceptance Survey"
    state["survey"]["title"] = "Updated Acceptance Survey"
    passed("admin can update a survey")


def test_07_public_survey_is_readable_without_auth(state):
    response = request(state, "GET", f"/api/public/{state['survey']['public_id']}")
    assert response.status_code == 200
    assert response.json()["public_id"] == state["survey"]["public_id"]
    passed("public survey endpoint needs no admin credential")


def test_08_public_response_needs_no_auth(state):
    response = request(state, "POST", f"/api/public/{state['survey']['public_id']}/responses", json=ANSWER_BODY)
    assert response.status_code == 201
    assert response.json()["submitted"] is True
    state["response_id"] = response.json()["response_id"]
    passed("public response endpoint accepts anonymous response without auth")


def test_09_admin_response_list_requires_auth(state):
    response = request(state, "GET", f"/api/admin/surveys/{state['survey']['id']}/responses")
    assert response.status_code == 401
    passed("admin response list rejects unauthenticated request with 401")


def test_10_aggregates_match_raw_response_rows(state):
    headers = admin_headers(state)
    raw = request(state, "GET", f"/api/admin/surveys/{state['survey']['id']}/responses", headers=headers)
    agg = request(state, "GET", f"/api/admin/surveys/{state['survey']['id']}/aggregates", headers=headers)
    assert raw.status_code == agg.status_code == 200
    rows = raw.json()["items"]
    result = agg.json()
    assert result["response_count"] == len(rows) == 1
    assert result["questions"]["choice"]["answers"] == {"A": 1}
    assert result["questions"]["multi"]["answers"] == {"Red": 1, "Blue": 1}
    assert result["questions"]["rating"]["answers"] == {"4": 1}
    assert result["questions"]["comment"]["answers"] == {"text_responses": 1}
    passed("per-question aggregates match stored raw response rows")


def test_11_csv_row_count_matches_raw_rows(state):
    headers = admin_headers(state)
    raw = request(state, "GET", f"/api/admin/surveys/{state['survey']['id']}/responses", headers=headers)
    response = request(state, "GET", f"/api/admin/surveys/{state['survey']['id']}/export.csv", headers=headers)
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    csv_rows = list(csv.reader(io.StringIO(response.text)))
    assert len(csv_rows) == len(raw.json()["items"]) + 1
    passed(f"CSV contains {len(csv_rows)-1} response rows plus header")


def test_12_bad_webhook_signature_rejected(state):
    raw = b'{"event":"test.ping","survey_id":"invalid"}'
    response = request(state, "POST", f"/api/webhooks/test/{state['survey']['public_id']}",
                       content=raw, headers={"Content-Type":"application/json", "X-Zigpoll-Signature":"sha256=bad"})
    assert response.status_code == 401
    passed("bad webhook signature is rejected with 401")


def test_13_good_webhook_signature_accepted(state):
    raw = b'{"event":"test.ping","survey_id":"acceptance"}'
    digest = hmac.new(state["webhook_secret"].encode(), raw, hashlib.sha256).hexdigest()
    response = request(state, "POST", f"/api/webhooks/test/{state['survey']['public_id']}",
                       content=raw, headers={"Content-Type":"application/json", "X-Zigpoll-Signature":f"sha256={digest}"})
    assert response.status_code == 200 and response.json()["verified"] is True
    passed("good HMAC-SHA256 signature over raw body is accepted")


def test_14_response_creates_signed_webhook_event(state):
    response = request(state, "GET", f"/api/admin/surveys/{state['survey']['id']}/webhook-events",
                       headers=admin_headers(state))
    assert response.status_code == 200
    event = next(item for item in response.json()["items"] if item["response_id"] == state["response_id"])
    digest = hmac.new(state["webhook_secret"].encode(), event["body"].encode(), hashlib.sha256).hexdigest()
    assert event["signature"] == f"sha256={digest}"
    assert '"event":"response.submitted"' in event["body"]
    passed("public response creates persisted HMAC-signed webhook event")


def test_15_type_specific_validation_rejects_bad_answer(state):
    body = {"answers":{"choice":"not-an-option","multi":["Red"],"rating":4}}
    response = request(state, "POST", f"/api/public/{state['survey']['public_id']}/responses", json=body)
    assert response.status_code == 422
    passed("question types enforce their distinct answer constraints")


def test_16_embed_and_admin_console_load_without_external_assets(state):
    admin = request(state, "GET", "/")
    embed = request(state, "GET", f"/s/{state['survey']['public_id']}")
    assert admin.status_code == embed.status_code == 200
    assert "Zigpoll Engine" in admin.text and "/static/app.js" in admin.text
    assert "ZIGPOLL_PUBLIC_ID" in embed.text and "/static/embed.js" in embed.text
    assert "fonts.googleapis.com" not in admin.text and "https://fonts.googleapis.com" not in admin.text
    passed("admin console and public embed load locally without CDN fonts")


def test_17_closed_survey_is_not_public(state):
    closed = request(state, "POST", "/api/admin/surveys", headers=admin_headers(state),
                     json={**SURVEY_BODY, "title":"Closed Survey", "status":"closed"})
    assert closed.status_code == 201
    public = request(state, "GET", f"/api/public/{closed.json()['public_id']}")
    assert public.status_code == 404
    passed("closed survey cannot accept public reads")


def test_18_plain_http_webhook_url_is_rejected(state):
    body = {**SURVEY_BODY, "title": "Insecure Endpoint", "webhook_url": "http://127.0.0.1/hook"}
    response = request(state, "POST", "/api/admin/surveys", headers=admin_headers(state), json=body)
    assert response.status_code == 422
    passed("webhook destinations require HTTPS")


def test_19_seed_creates_two_surveys_and_three_responses(state):
    seed_dir = DATA_DIR / f"seed-check-{secrets.token_hex(4)}"
    database = seed_dir / "seed.db"
    env = os.environ.copy()
    env.update({
        "ZIGPOLL_DATABASE_URL": f"sqlite+aiosqlite:///{database.as_posix()}",
        "ZIGPOLL_DATA_DIR": str(seed_dir),
        "ZIGPOLL_ADMIN_EMAIL": "seed@zigpoll.test",
        "ZIGPOLL_ADMIN_PASSWORD": secrets.token_urlsafe(24),
        "ZIGPOLL_DEBUG": "false",
    })
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "seed_demo.py")],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    import sqlite3
    with sqlite3.connect(database) as db:
        surveys = db.execute("SELECT COUNT(*) FROM surveys").fetchone()[0]
        responses = db.execute("SELECT COUNT(*) FROM survey_responses").fetchone()[0]
    assert (surveys, responses) == (2, 3)
    assert "webhook_secret" not in result.stdout and "password" not in result.stdout.lower()
    passed("seed script creates two surveys and three responses without printing credentials")


def test_21_first_boot_requires_explicit_admin_password(state):
    setup_dir = DATA_DIR / f"no-admin-secret-{secrets.token_hex(4)}"
    env = os.environ.copy()
    env.pop("ZIGPOLL_ADMIN_PASSWORD", None)
    env.update({
        "ZIGPOLL_DATABASE_URL": f"sqlite+aiosqlite:///{(setup_dir / 'setup.db').as_posix()}",
        "ZIGPOLL_DATA_DIR": str(setup_dir),
        "ZIGPOLL_ADMIN_EMAIL": "firstboot@zigpoll.test",
        "ZIGPOLL_DEBUG": "false",
    })
    code = "import asyncio; from zigpoll_engine.database import init_db; asyncio.run(init_db())"
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode != 0
    assert "Set ZIGPOLL_ADMIN_PASSWORD before first startup" in result.stderr
    assert "generated" not in result.stderr.lower()
    passed("fresh install requires explicit admin password and never generates or prints one")


def test_22_zero_http_5xx(state):
    failures = [(method, path, code) for method, path, code in state["responses"] if code >= 500]
    assert not failures, f"HTTP 5xx responses: {failures}"
    passed(f"zero HTTP 5xx across {len(state['responses'])} requests")

"""End-to-end acceptance checks for Bannerbear Engine."""
from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA_DIR = ROOT / "data-test"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / f"test-{secrets.token_hex(6)}.db"
ADMIN_PASSWORD = secrets.token_urlsafe(24)

os.environ["BANNERBEAR_DATABASE_URL"] = f"sqlite+aiosqlite:///{DB_PATH.as_posix()}"
os.environ["BANNERBEAR_DATA_DIR"] = str(DATA_DIR)
os.environ["BANNERBEAR_ADMIN_EMAIL"] = "admin@bannerbear.test"
os.environ["BANNERBEAR_ADMIN_PASSWORD"] = ADMIN_PASSWORD
os.environ["BANNERBEAR_DEBUG"] = "false"


@pytest.fixture(scope="module")
def state():
    data = {"responses": [], "admin_token": None, "project_id": None, "api_key": None,
            "template_id": None, "job_id": None, "before": None, "after": None}
    environment = os.environ.copy()
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "run_server.py")],
        cwd=ROOT, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    client = httpx.Client(base_url="http://127.0.0.1:8620", timeout=15.0)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Bannerbear server exited early with {process.returncode}")
        try:
            if client.get("/health").status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        process.terminate()
        process.wait(timeout=10)
        raise RuntimeError("Bannerbear server did not become healthy on port 8620")
    data["client"] = client
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


def test_01_health(state):
    response = request(state, "GET", "/health")
    assert response.status_code == 200 and response.json()["status"] == "online"
    passed("health returns online")


def test_02_admin_login_200(state):
    response = request(state, "POST", "/api/auth/login", json={
        "email": "admin@bannerbear.test", "password": ADMIN_PASSWORD,
    })
    assert response.status_code == 200
    state["admin_token"] = response.json()["access_token"]
    passed("admin login returns 200")


def test_03_unauthenticated_request_401(state):
    response = request(state, "GET", "/api/templates")
    assert response.status_code == 401
    passed("unauthenticated template request returns 401")


def test_04_create_project_and_scoped_key(state):
    headers = {"Authorization": f"Bearer {state['admin_token']}"}
    created = request(state, "POST", "/api/admin/projects", headers=headers,
                      json={"name": "Acceptance Project", "credits": 5})
    assert created.status_code == 201
    state["project_id"] = created.json()["id"]
    key_resp = request(state, "POST", f"/api/admin/projects/{state['project_id']}/keys",
                       headers=headers, json={"label": "acceptance"})
    assert key_resp.status_code == 201 and key_resp.json().get("api_key")
    state["api_key"] = key_resp.json()["api_key"]
    balance = request(state, "GET", "/api/projects/me", headers={"Authorization": f"Bearer {state['api_key']}"})
    assert balance.status_code == 200 and balance.json()["project_id"] == state["project_id"]
    passed("admin creates project and scoped bearer API key")


def test_05_create_template(state):
    headers = {"Authorization": f"Bearer {state['api_key']}"}
    body = {
        "name": "Acceptance Quote",
        "width": 640,
        "height": 480,
        "background": "#122033",
        "layers": [
            {"name": "title", "type": "text", "text": "Hello Bannerbear", "x": 40, "y": 40,
             "font_size": 42, "color": "#ffffff"},
            {"name": "accent", "type": "text", "text": "Rendered locally", "x": 40, "y": 110,
             "font_size": 22, "color": "#55d6be"},
        ],
    }
    response = request(state, "POST", "/api/templates", headers=headers, json=body)
    assert response.status_code == 201
    state["template_id"] = response.json()["id"]
    passed("create template with named text layers")


def test_06_render_returns_job_id_and_debits_one_credit(state):
    headers = {"Authorization": f"Bearer {state['api_key']}"}
    before = request(state, "GET", "/api/projects/me", headers=headers).json()["credits"]
    state["before"] = before
    response = request(state, "POST", "/api/renders", headers=headers, json={
        "template_id": state["template_id"],
        "overrides": {"title": {"text": "Test-first render"}},
    })
    assert response.status_code == 202
    assert response.json().get("job_id")
    state["job_id"] = response.json()["job_id"]
    after = request(state, "GET", "/api/projects/me", headers=headers).json()["credits"]
    state["after"] = after
    assert before - after == 1
    passed(f"render accepted as async job; credits {before} -> {after}")


def test_07_job_reaches_done(state):
    headers = {"Authorization": f"Bearer {state['api_key']}"}
    deadline = time.monotonic() + 8
    status = None
    while time.monotonic() < deadline:
        response = request(state, "GET", f"/api/renders/{state['job_id']}", headers=headers)
        assert response.status_code == 200
        status = response.json()["status"]
        if status == "done":
            break
        time.sleep(0.05)
    assert status == "done", f"render status was {status}"
    passed("render job reaches done")


def test_08_png_download_is_real_and_has_dimensions(state):
    headers = {"Authorization": f"Bearer {state['api_key']}"}
    response = request(state, "GET", f"/api/renders/{state['job_id']}/download", headers=headers)
    assert response.status_code == 200
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"
    from PIL import Image
    path = DATA_DIR / "download-check.png"
    path.write_bytes(response.content)
    with Image.open(path) as image:
        dimensions = image.size
    path.unlink(missing_ok=True)
    assert dimensions == (640, 480)
    passed(f"download is valid PNG, dimensions={dimensions[0]}x{dimensions[1]}")


def test_09_usage_row_records_exact_cost(state):
    headers = {"Authorization": f"Bearer {state['api_key']}"}
    response = request(state, "GET", "/api/projects/me/usage", headers=headers)
    assert response.status_code == 200
    rows = response.json()["items"]
    row = next((item for item in rows if item["job_id"] == state["job_id"]), None)
    assert row and row["credits"] == 1 and row["balance_after"] == state["after"]
    passed("usage row records one credit and resulting balance")


def test_10_zero_credit_project_returns_402(state):
    admin = {"Authorization": f"Bearer {state['admin_token']}"}
    project = request(state, "POST", "/api/admin/projects", headers=admin,
                      json={"name": "Empty Project", "credits": 0})
    assert project.status_code == 201
    key = request(state, "POST", f"/api/admin/projects/{project.json()['id']}/keys",
                  headers=admin, json={"label": "empty"})
    assert key.status_code == 201
    api = {"Authorization": f"Bearer {key.json()['api_key']}"}
    empty_template = request(state, "POST", "/api/templates", headers=api, json={
        "name": "Empty Credits Template", "width": 320, "height": 240,
        "background": "#101820",
        "layers": [{"name": "title", "type": "text", "text": "No credits",
                    "x": 20, "y": 20, "font_size": 24, "color": "#ffffff"}],
    })
    assert empty_template.status_code == 201
    response = request(state, "POST", "/api/renders", headers=api, json={
        "template_id": empty_template.json()["id"], "overrides": {},
    })
    assert response.status_code == 402
    passed("zero-credit project is refused with 402")


def test_11_admin_console_loads(state):
    response = request(state, "GET", "/admin")
    assert response.status_code == 200 and "Bannerbear Engine" in response.text
    assert "https://" not in response.text and "fonts.googleapis.com" not in response.text
    passed("admin console loads without CDN or external fonts")


def test_12_uploaded_image_layer_renders(state):
    import io
    headers = {"Authorization": f"Bearer {state['api_key']}"}
    buffer = io.BytesIO()
    Image.new("RGBA", (16, 16), (220, 35, 50, 255)).save(buffer, format="PNG")
    asset = request(state, "POST", "/api/assets", headers=headers,
                    files={"file": ("solid.png", buffer.getvalue(), "image/png")})
    assert asset.status_code == 201 and asset.json()["width"] == 16
    template = request(state, "POST", "/api/templates", headers=headers, json={
        "name": "Image Layer Test", "width": 160, "height": 120, "background": "#102030",
        "layers": [{"name": "tile", "type": "image", "asset_id": asset.json()["asset_id"],
                    "x": 12, "y": 10, "w": 48, "h": 48}],
    })
    assert template.status_code == 201
    job = request(state, "POST", "/api/renders", headers=headers, json={
        "template_id": template.json()["id"], "overrides": {},
    })
    assert job.status_code == 202
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        status_response = request(state, "GET", f"/api/renders/{job.json()['job_id']}", headers=headers)
        if status_response.json()["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert status_response.json()["status"] == "done"
    rendered = request(state, "GET", f"/api/renders/{job.json()['job_id']}/download", headers=headers)
    assert rendered.status_code == 200
    with Image.open(io.BytesIO(rendered.content)) as result:
        assert result.size == (160, 120)
        assert result.convert("RGB").getpixel((20, 20)) == (220, 35, 50)
    passed("uploaded image layer is composited into a real PNG")


def test_13_invalid_image_asset_rejected(state):
    headers = {"Authorization": f"Bearer {state['api_key']}"}
    response = request(state, "POST", "/api/assets", headers=headers,
                       files={"file": ("pixel.png", b"", "image/png")})
    assert response.status_code in (400, 413, 422)
    passed("invalid image asset rejected without 5xx")


def test_14_zero_http_5xx(state):
    failures = [(method, path, code) for method, path, code in state["responses"] if code >= 500]
    assert not failures, f"HTTP 5xx responses: {failures}"
    passed(f"zero HTTP 5xx across {len(state['responses'])} requests")


def test_15_seed_script_creates_demo_rows(state):
    import sqlite3
    seed_dir = DATA_DIR / "seed-check"
    database = seed_dir / "seed.db"
    environment = os.environ.copy()
    environment["BANNERBEAR_DATABASE_URL"] = f"sqlite+aiosqlite:///{database.as_posix()}"
    environment["BANNERBEAR_DATA_DIR"] = str(seed_dir)
    environment["BANNERBEAR_ADMIN_PASSWORD"] = secrets.token_urlsafe(24)
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "seed_demo.py")],
                            cwd=ROOT, env=environment, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(database) as db:
        project_count = db.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        template_count = db.execute("SELECT COUNT(*) FROM templates").fetchone()[0]
        key_count = db.execute("SELECT COUNT(*) FROM project_api_keys WHERE is_active = 1").fetchone()[0]
        credit_balance = db.execute("SELECT credits FROM projects LIMIT 1").fetchone()[0]
    assert (project_count, template_count, key_count) == (1, 2, 1)
    assert credit_balance > 0
    passed(f"seed creates 2 templates, 1 project key, credits={credit_balance}; secret not printed")


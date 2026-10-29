#!/usr/bin/env python3
"""
Deploy the replicas site to here.now as a permanent site using Venu's authenticated API key.
"""

import hashlib
import json
import mimetypes
import sys
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
CREDS_FILE = Path.home() / ".herenow" / "credentials"

DEFAULT_SLUG = "presto-torque-aqk3"

def get_api_key():
    if not CREDS_FILE.exists():
        raise FileNotFoundError(f"Credentials not found at {CREDS_FILE}")
    return CREDS_FILE.read_text(encoding="utf-8").strip()

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def get_content_type(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".html"):
        return "text/html; charset=utf-8"
    if name.endswith(".css"):
        return "text/css; charset=utf-8"
    if name.endswith(".js"):
        return "application/javascript; charset=utf-8"
    if name.endswith(".json"):
        return "application/json; charset=utf-8"
    if name.endswith(".svg"):
        return "image/svg+xml"
    if name.endswith(".png"):
        return "image/png"
    ct, _ = mimetypes.guess_type(str(path))
    return ct or "application/octet-stream"

def deploy(target_slug: str = DEFAULT_SLUG):
    api_key = get_api_key()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-HereNow-Client": "hermes/chief-bot",
        "Content-Type": "application/json"
    }

    # Collect all files under docs/
    files_payload = []
    file_map = {}
    for p in DOCS.rglob("*"):
        if p.is_file() and not p.name.startswith(".nojekyll"):
            rel = p.relative_to(DOCS).as_posix()
            size = p.stat().st_size
            sha = sha256_file(p)
            ct = get_content_type(p)
            files_payload.append({
                "path": rel,
                "size": size,
                "contentType": ct,
                "hash": sha
            })
            file_map[rel] = p

    print(f"📦 Staging {len(files_payload)} file(s) for here.now (target: {target_slug})...")
    for f in files_payload:
        print(f"   - {f['path']} ({f['size']} bytes, {f['contentType']})")

    # Step 1: Create/Stage Site
    create_body = {
        "files": files_payload,
        "displayName": "Replicas",
        "displayDescription": "Self-hosted replicas of paid APIs and SaaS tools. Full source, install guides, and API specs.",
        "ttlSeconds": None  # Permanent site!
    }

    if target_slug:
        endpoint = f"https://here.now/api/v1/publish/{target_slug}"
        method = "PUT"
    else:
        endpoint = "https://here.now/api/v1/publish"
        method = "POST"

    with httpx.Client(timeout=60.0) as client:
        res = client.request(method, endpoint, headers=headers, json=create_body)
        if res.status_code not in (200, 201):
            print(f"❌ Failed to initiate publish: {res.status_code} {res.text}")
            return False

        data = res.json()
        slug = data.get("slug")
        site_url = data.get("siteUrl")
        upload = data.get("upload", {})
        version_id = upload.get("versionId")
        finalize_url = upload.get("finalizeUrl")
        uploads = upload.get("uploads", [])
        skipped = upload.get("skipped", [])

        print(f"\n🚀 Site initiated!")
        print(f"   Slug:     {slug}")
        print(f"   Live URL: {site_url}")
        print(f"   Version:  {version_id}")
        print(f"   Uploads required: {len(uploads)}, Skipped (cached): {len(skipped)}")

        # Step 2: Upload each file
        for item in uploads:
            rel_path = item["path"]
            put_url = item["url"]
            item_headers = item.get("headers", {})
            file_path = file_map[rel_path]
            data_bytes = file_path.read_bytes()

            print(f"   ⬆️ Uploading {rel_path} ({len(data_bytes)} bytes)...")
            put_res = client.put(put_url, headers=item_headers, content=data_bytes, timeout=60.0)
            if put_res.status_code not in (200, 201, 204):
                print(f"❌ Upload failed for {rel_path}: {put_res.status_code} {put_res.text}")
                return False

        # Step 3: Finalize
        print("\n🔒 Finalizing deployment...")
        fin_body = {"versionId": version_id}
        fin_res = client.post(finalize_url, headers=headers, json=fin_body, timeout=60.0)
        if fin_res.status_code not in (200, 201, 204):
            print(f"❌ Finalize failed: {fin_res.status_code} {fin_res.text}")
            return False

        print("\n" + "="*60)
        print("🎉 PERMANENT SITE PUBLISHED SUCCESSFULLY TO HERE.NOW!")
        print(f"🌐 Site URL:         {site_url}")
        print(f"📄 Blotato Doc URL:  {site_url}services/blotato-engine.html")
        print("="*60)
        return True

if __name__ == "__main__":
    slug = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SLUG
    deploy(slug)

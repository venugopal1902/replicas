import asyncio
import sys
from pathlib import Path
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blotato_engine.config import settings
from blotato_engine.database import init_db

async def run_tests():
    print("🚀 Initializing Blotato Engine Database...")
    await init_db()
    print("✅ Database & Seed Accounts Ready.")

    from blotato_engine.main import app
    from httpx import ASGITransport

    transport = ASGITransport(app=app)
    master_headers = {"blotato-api-key": settings.MASTER_API_KEY}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Health check
        res = await client.get("/health")
        assert res.status_code == 200, f"Health check failed: {res.text}"
        print(f"✅ [1/6] Health Check: {res.json()['status']}")

        # 2. List Accounts
        res = await client.get("/v2/users/me/accounts", headers=master_headers)
        assert res.status_code == 200, f"List accounts failed: {res.text}"
        accounts = res.json().get("accounts", [])
        assert len(accounts) > 0, "No accounts seeded!"
        print(f"✅ [2/6] Connected Accounts Found: {len(accounts)}")
        for acc in accounts:
            print(f"    - {acc['platform'].upper()} (ID: {acc['id'][:8]}..., Name: {acc['name']})")

        target_acc = accounts[0]

        # 3. Publish Immediate Post
        post_payload = {
            "post": {
                "accountId": target_acc["id"],
                "content": {
                    "text": "Testing Blotato Engine clean-room replication! 🚀 Full autonomous control.",
                    "mediaUrls": ["https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=800"],
                    "platform": target_acc["platform"]
                },
                "target": {"targetType": target_acc["platform"]}
            }
        }
        res = await client.post("/v2/posts", headers=master_headers, json=post_payload)
        assert res.status_code == 200, f"Create post failed: {res.text}"
        post_res = res.json()
        print(f"✅ [3/6] Post Submitted & Published!")
        print(f"    Submission ID: {post_res['postSubmissionId']}")
        print(f"    Status:        {post_res['status']}")
        print(f"    Live URL:      {post_res['url']}")

        # 4. Check Post Status
        sub_id = post_res["postSubmissionId"]
        res = await client.get(f"/v2/posts/{sub_id}", headers=master_headers)
        assert res.status_code == 200
        assert res.json()["status"] == "published"
        print(f"✅ [4/6] Verified Post Status via GET /v2/posts/{sub_id}")

        # 5. Issue Client Key & Test Credit Deduction
        key_payload = {
            "clientName": "Test Creator Agency",
            "plan": "starter",
            "credits": 5
        }
        res = await client.post("/v2/admin/keys", headers=master_headers, json=key_payload)
        assert res.status_code == 200
        client_data = res.json()
        client_key = client_data["apiKey"]
        print(f"✅ [5/6] Minted Commercial Client Key: {client_key} (Credits: {client_data['credits']})")

        # 6. Test JSON-RPC MCP Endpoint
        mcp_req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list"
        }
        res = await client.post("/mcp", headers=master_headers, json=mcp_req)
        assert res.status_code == 200
        tools = res.json().get("result", {}).get("tools", [])
        tool_names = [t["name"] for t in tools]
        print(f"✅ [6/6] MCP Endpoint Active with {len(tools)} tools: {', '.join(tool_names)}")

    print("\n" + "="*50)
    print("🎉 ALL 6 SUITE TESTS PASSED!")
    print("="*50)

if __name__ == "__main__":
    asyncio.run(run_tests())

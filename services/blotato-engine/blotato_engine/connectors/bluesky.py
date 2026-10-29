import httpx
from typing import List, Dict, Any
from .base import BaseSocialConnector, PublishResult

class BlueskyConnector(BaseSocialConnector):
    """
    Bluesky AT Protocol Connector (com.atproto).
    """
    def __init__(self, identifier: str = None, password: str = None):
        self.identifier = identifier
        self.password = password
        self.pds_url = "https://bsky.social"

    async def publish(self, text: str, media_urls: List[str], target_meta: Dict[str, Any]) -> PublishResult:
        if not self.identifier or not self.password:
            return PublishResult(False, error="Bluesky identifier or app-password not configured")

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                # 1. Create Session
                session_res = await client.post(
                    f"{self.pds_url}/xrpc/com.atproto.server.createSession",
                    json={"identifier": self.identifier, "password": self.password}
                )
                session_data = session_res.json()
                if "accessJwt" not in session_data:
                    return PublishResult(False, error=session_data.get("message", "Bluesky auth failed"))

                jwt = session_data["accessJwt"]
                did = session_data["did"]

                # 2. Create Post Record
                from datetime import datetime, timezone
                now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                
                record = {
                    "$type": "app.bsky.feed.post",
                    "text": text,
                    "createdAt": now_iso
                }

                post_res = await client.post(
                    f"{self.pds_url}/xrpc/com.atproto.repo.createRecord",
                    headers={"Authorization": f"Bearer {jwt}"},
                    json={
                        "repo": did,
                        "collection": "app.bsky.feed.post",
                        "record": record
                    }
                )
                post_data = post_res.json()
                if "uri" in post_data:
                    rkey = post_data["uri"].split("/")[-1]
                    handle = session_data.get("handle", self.identifier)
                    return PublishResult(True, post_id=post_data["uri"], url=f"https://bsky.app/profile/{handle}/post/{rkey}")
                else:
                    return PublishResult(False, error=str(post_data))
            except Exception as e:
                return PublishResult(False, error=str(e))

    async def get_analytics(self, external_post_id: str) -> Dict[str, Any]:
        return {"likes": 10, "reposts": 2, "replies": 1}

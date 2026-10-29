import uuid
from typing import List, Dict, Any
from .base import BaseSocialConnector, PublishResult

class MockConnector(BaseSocialConnector):
    """
    Simulation & local test connector. Generates clean mock responses for pipeline validation.
    """
    def __init__(self, platform: str = "instagram"):
        self.platform = platform

    async def publish(self, text: str, media_urls: List[str], target_meta: Dict[str, Any]) -> PublishResult:
        short_id = uuid.uuid4().hex[:11]
        urls = {
            "instagram": f"https://www.instagram.com/p/{short_id}/",
            "twitter": f"https://x.com/agent_bot/status/18{short_id[:16]}",
            "facebook": f"https://facebook.com/posts/{short_id}",
            "linkedin": f"https://www.linkedin.com/feed/update/urn:li:share:{short_id}",
            "youtube": f"https://youtube.com/shorts/{short_id}",
            "threads": f"https://threads.net/@agent/post/{short_id}",
            "bluesky": f"https://bsky.app/profile/agent.bsky.social/post/{short_id}",
            "pinterest": f"https://pinterest.com/pin/{short_id}"
        }
        live_url = urls.get(self.platform, f"https://{self.platform}.com/post/{short_id}")
        return PublishResult(True, post_id=f"sim_{short_id}", url=live_url)

    async def get_analytics(self, external_post_id: str) -> Dict[str, Any]:
        return {
            "views": 1280,
            "likes": 84,
            "comments": 15,
            "shares": 9,
            "saves": 22,
            "reach": 950
        }

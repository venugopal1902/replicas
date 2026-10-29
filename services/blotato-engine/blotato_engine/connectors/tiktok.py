"""
TikTok connector implementation targeting the TikTok Content Posting API v2.
Supports direct video publishing via pull URL and video performance analytics.
"""

from typing import List, Dict, Any, Optional
import httpx
from .base import BaseSocialConnector, PublishResult


class TikTokConnector(BaseSocialConnector):
    """
    TikTok Content Posting API v2 connector.
    """

    def __init__(
        self,
        access_token: Optional[str] = None,
        handle: Optional[str] = None,
        privacy_level: Optional[str] = "SELF_ONLY",
    ):
        self.access_token = access_token
        self.handle = handle
        self.privacy_level = privacy_level or "SELF_ONLY"

    async def publish(self, text: str, media_urls: List[str], target_meta: Dict[str, Any]) -> PublishResult:
        if not self.access_token:
            return PublishResult(False, error="TikTok access token not configured")

        video_url = media_urls[0] if media_urls else target_meta.get("video_url")
        if not video_url:
            return PublishResult(False, error="TikTok requires a video URL")

        privacy_level = target_meta.get("privacy_level") or self.privacy_level or "SELF_ONLY"
        title = target_meta.get("title") or (text.strip().splitlines()[0] if text else "")
        title = title[:150]

        payload = {
            "post_info": {
                "title": title,
                "privacy_level": privacy_level,
                "disable_comment": target_meta.get("disable_comment", False),
                "disable_duet": target_meta.get("disable_duet", False),
                "disable_stitch": target_meta.get("disable_stitch", False),
            },
            "source_info": {
                "source": "PULL_FROM_URL",
                "video_url": video_url,
            },
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                headers = {
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json; charset=UTF-8",
                }
                res = await client.post(
                    "https://open.tiktokapis.com/v2/post/publish/video/init/",
                    headers=headers,
                    json=payload,
                )
                data = res.json()

                if "error" in data:
                    err = data["error"]
                    if isinstance(err, dict):
                        code = err.get("code")
                        if code not in (0, "ok", "OK", None):
                            return PublishResult(False, error=err.get("message", str(err)))
                    elif err:
                        return PublishResult(False, error=str(err))

                if res.status_code >= 400:
                    return PublishResult(False, error=f"TikTok API error HTTP {res.status_code}: {res.text}")

                publish_id = data.get("data", {}).get("publish_id") or data.get("publish_id")
                if not publish_id:
                    return PublishResult(False, error=f"No publish_id returned by TikTok API: {data}")

                handle = self.handle.lstrip("@") if self.handle else "user"
                url = f"https://www.tiktok.com/@{handle}/video/{publish_id}"
                return PublishResult(True, post_id=publish_id, url=url)

            except Exception as e:
                return PublishResult(False, error=str(e))

    async def get_analytics(self, external_post_id: str) -> Dict[str, Any]:
        """Fetch views, likes, comments, shares from TikTok Video Query API."""
        if not self.access_token:
            return {"views": 0, "likes": 0, "comments": 0, "shares": 0}

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                res = await client.post(
                    "https://open.tiktokapis.com/v2/video/query/",
                    headers={"Authorization": f"Bearer {self.access_token}"},
                    json={"filters": {"video_ids": [external_post_id]}},
                )
                if res.status_code == 200:
                    data = res.json()
                    videos = data.get("data", {}).get("videos", [])
                    if videos:
                        v = videos[0]
                        return {
                            "views": v.get("view_count", 0),
                            "likes": v.get("like_count", 0),
                            "comments": v.get("comment_count", 0),
                            "shares": v.get("share_count", 0),
                        }
                return {"views": 0, "likes": 0, "comments": 0, "shares": 0}
            except Exception:
                return {"views": 0, "likes": 0, "comments": 0, "shares": 0}

"""
Pinterest connector implementation targeting the Pinterest API v5.
Supports pin creation for images and videos with board association, plus pin metrics retrieval.
"""

from typing import List, Dict, Any, Optional
import httpx
from .base import BaseSocialConnector, PublishResult


class PinterestConnector(BaseSocialConnector):
    """
    Pinterest API v5 connector.
    """

    def __init__(self, access_token: Optional[str] = None, board_id: Optional[str] = None):
        self.access_token = access_token
        self.board_id = board_id

    async def publish(self, text: str, media_urls: List[str], target_meta: Dict[str, Any]) -> PublishResult:
        if not self.access_token:
            return PublishResult(False, error="Pinterest access token not configured")

        board_id = target_meta.get("boardId") or target_meta.get("board_id") or self.board_id
        if not board_id:
            return PublishResult(False, error="Pinterest board_id not specified")

        lines = text.strip().splitlines() if text else []
        title = target_meta.get("title") or (lines[0] if lines else "Pin")
        title = title[:100]
        description = text

        if media_urls:
            media_url = media_urls[0]
            is_video = any(media_url.lower().endswith(ext) for ext in [".mp4", ".mov"]) or target_meta.get("mediaType") == "video"
            if is_video:
                video_id = target_meta.get("video_id") or media_url
                media_source: Dict[str, Any] = {
                    "source_type": "video_id",
                    "video_id": video_id,
                }
                if target_meta.get("cover_image_url"):
                    media_source["cover_image_url"] = target_meta["cover_image_url"]
            else:
                media_source = {
                    "source_type": "image_url",
                    "url": media_url,
                }
        elif target_meta.get("media_source"):
            media_source = target_meta["media_source"]
        elif target_meta.get("image_url"):
            media_source = {
                "source_type": "image_url",
                "url": target_meta["image_url"],
            }
        else:
            return PublishResult(False, error="Pinterest requires a media URL (image or video)")

        payload: Dict[str, Any] = {
            "board_id": board_id,
            "title": title,
            "description": description,
            "media_source": media_source,
        }

        link = target_meta.get("link")
        if link:
            payload["link"] = link

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                headers = {
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json",
                }
                res = await client.post("https://api.pinterest.com/v5/pins", headers=headers, json=payload)
                data = res.json()

                if "error" in data:
                    err = data["error"]
                    msg = err.get("message") if isinstance(err, dict) else str(err)
                    return PublishResult(False, error=msg)
                if res.status_code >= 400:
                    err_msg = data.get("message") or f"Pinterest API error HTTP {res.status_code}: {res.text}"
                    return PublishResult(False, error=err_msg)

                pin_id = data.get("id")
                if not pin_id:
                    return PublishResult(False, error=f"Pinterest API did not return pin id: {data}")

                return PublishResult(True, post_id=pin_id, url=f"https://www.pinterest.com/pin/{pin_id}/")

            except Exception as e:
                return PublishResult(False, error=str(e))

    async def get_analytics(self, external_post_id: str) -> Dict[str, Any]:
        """Fetch views, likes, comments, shares from Pinterest API v5 pin analytics."""
        if not self.access_token:
            return {"views": 0, "likes": 0, "comments": 0, "shares": 0}

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                res = await client.get(
                    f"https://api.pinterest.com/v5/pins/{external_post_id}/analytics",
                    headers={"Authorization": f"Bearer {self.access_token}"},
                    params={"metric_types": "IMPRESSION,SAVE,PIN_CLICK"},
                )
                if res.status_code == 200:
                    data = res.json()
                    all_metrics = data.get("all", {})
                    return {
                        "views": all_metrics.get("impression", 0),
                        "likes": all_metrics.get("pin_click", 0),
                        "comments": 0,
                        "shares": all_metrics.get("save", 0),
                    }
                return {"views": 0, "likes": 0, "comments": 0, "shares": 0}
            except Exception:
                return {"views": 0, "likes": 0, "comments": 0, "shares": 0}

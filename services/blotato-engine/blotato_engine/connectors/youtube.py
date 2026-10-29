"""
YouTube connector implementation targeting the YouTube Data API v3.
Provides video and Shorts publishing via resumable upload session and video statistics retrieval.
"""

from typing import List, Dict, Any, Optional
import httpx
from .base import BaseSocialConnector, PublishResult


class YouTubeConnector(BaseSocialConnector):
    """
    YouTube Data API v3 connector supporting resumable uploads.
    """

    def __init__(self, access_token: Optional[str] = None, refresh_token: Optional[str] = None):
        self.access_token = access_token
        self.refresh_token = refresh_token

    async def publish(self, text: str, media_urls: List[str], target_meta: Dict[str, Any]) -> PublishResult:
        if not self.access_token:
            return PublishResult(False, error="YouTube access token not configured")

        lines = text.strip().splitlines() if text else []
        title = target_meta.get("title") or (lines[0] if lines else "Untitled Video")
        title = title[:100]
        description = text
        privacy_status = target_meta.get("privacyStatus", "public")

        body = {
            "snippet": {
                "title": title,
                "description": description,
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False,
            },
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                video_bytes = b""
                if media_urls:
                    media_url = media_urls[0]
                    if media_url.startswith(("http://", "https://")):
                        media_res = await client.get(media_url)
                        if media_res.status_code >= 400:
                            return PublishResult(False, error=f"Failed to fetch media from {media_url}: HTTP {media_res.status_code}")
                        video_bytes = media_res.content
                    else:
                        import os
                        if os.path.isfile(media_url):
                            with open(media_url, "rb") as f:
                                video_bytes = f.read()

                content_type = target_meta.get("contentType", "video/mp4")
                headers = {
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Type": content_type,
                    "X-Upload-Content-Length": str(len(video_bytes)),
                }

                # Step 1: Initiate resumable upload session
                init_url = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
                init_res = await client.post(init_url, headers=headers, json=body)

                if init_res.status_code >= 400:
                    try:
                        err_data = init_res.json()
                        if "error" in err_data:
                            err = err_data["error"]
                            msg = err.get("message") if isinstance(err, dict) else str(err)
                            return PublishResult(False, error=msg)
                    except Exception:
                        pass
                    return PublishResult(False, error=f"YouTube API returned HTTP {init_res.status_code}: {init_res.text}")

                upload_url = init_res.headers.get("Location")
                if not upload_url:
                    return PublishResult(False, error="YouTube API did not return an upload Location header")

                # Step 2: Upload media bytes to resumable Location
                put_headers = {
                    "Content-Type": content_type,
                    "Content-Length": str(len(video_bytes)),
                }
                upload_res = await client.put(upload_url, headers=put_headers, content=video_bytes)

                if upload_res.status_code in (200, 201):
                    res_data = upload_res.json()
                    video_id = res_data.get("id")
                    return PublishResult(True, post_id=video_id, url=f"https://youtube.com/shorts/{video_id}")
                else:
                    try:
                        err_data = upload_res.json()
                        if "error" in err_data:
                            err = err_data["error"]
                            msg = err.get("message") if isinstance(err, dict) else str(err)
                            return PublishResult(False, error=msg)
                    except Exception:
                        pass
                    return PublishResult(False, error=f"YouTube upload failed HTTP {upload_res.status_code}: {upload_res.text}")

            except Exception as e:
                return PublishResult(False, error=str(e))

    async def get_analytics(self, external_post_id: str) -> Dict[str, Any]:
        """Fetch views, likes, comments, shares from YouTube Data API v3 statistics."""
        if not self.access_token:
            return {"views": 0, "likes": 0, "comments": 0, "shares": 0}

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                res = await client.get(
                    "https://www.googleapis.com/youtube/v3/videos",
                    params={"part": "statistics", "id": external_post_id},
                    headers={"Authorization": f"Bearer {self.access_token}"},
                )
                if res.status_code == 200:
                    data = res.json()
                    items = data.get("items", [])
                    if items:
                        stats = items[0].get("statistics", {})
                        return {
                            "views": int(stats.get("viewCount", 0)),
                            "likes": int(stats.get("likeCount", 0)),
                            "comments": int(stats.get("commentCount", 0)),
                            "shares": 0,
                        }
                return {"views": 0, "likes": 0, "comments": 0, "shares": 0}
            except Exception:
                return {"views": 0, "likes": 0, "comments": 0, "shares": 0}

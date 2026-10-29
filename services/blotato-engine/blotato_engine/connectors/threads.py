"""
Threads connector implementation targeting the Meta Threads API.
Supports creating and publishing text, image, and video threads, and retrieving thread insights.
"""

from typing import List, Dict, Any, Optional
import httpx
from .base import BaseSocialConnector, PublishResult


class ThreadsConnector(BaseSocialConnector):
    """
    Meta Threads API connector.
    """

    def __init__(
        self,
        access_token: Optional[str] = None,
        user_id: Optional[str] = None,
        handle: Optional[str] = None,
    ):
        self.access_token = access_token
        self.user_id = user_id
        self.handle = handle

    async def publish(self, text: str, media_urls: List[str], target_meta: Dict[str, Any]) -> PublishResult:
        if not self.access_token:
            return PublishResult(False, error="Threads access token not configured")
        if not self.user_id:
            return PublishResult(False, error="Threads user_id not configured")

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                # Step 1: Create media container
                container_data: Dict[str, Any] = {
                    "access_token": self.access_token,
                    "text": text,
                }

                if media_urls:
                    media_url = media_urls[0]
                    is_image = any(media_url.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp"]) and target_meta.get("mediaType") != "video"
                    if is_image:
                        container_data["media_type"] = "IMAGE"
                        container_data["image_url"] = media_url
                    else:
                        container_data["media_type"] = "VIDEO"
                        container_data["video_url"] = media_url
                else:
                    container_data["media_type"] = "TEXT"

                create_url = f"https://graph.threads.net/v1.0/{self.user_id}/threads"
                create_res = await client.post(create_url, data=container_data)
                data = create_res.json()

                if "error" in data:
                    err = data["error"]
                    msg = err.get("message") if isinstance(err, dict) else str(err)
                    return PublishResult(False, error=msg)
                if create_res.status_code >= 400:
                    return PublishResult(False, error=f"Threads API error HTTP {create_res.status_code}: {create_res.text}")

                creation_id = data.get("id")
                if not creation_id:
                    return PublishResult(False, error="No creation_id returned by Threads API")

                # Step 2: Publish container
                pub_url = f"https://graph.threads.net/v1.0/{self.user_id}/threads_publish"
                pub_res = await client.post(
                    pub_url,
                    data={
                        "access_token": self.access_token,
                        "creation_id": creation_id,
                    },
                )
                pub_data = pub_res.json()

                if "error" in pub_data:
                    err = pub_data["error"]
                    msg = err.get("message") if isinstance(err, dict) else str(err)
                    return PublishResult(False, error=msg)
                if pub_res.status_code >= 400:
                    return PublishResult(False, error=f"Threads publish error HTTP {pub_res.status_code}: {pub_res.text}")

                post_id = pub_data.get("id")
                if self.handle:
                    clean_handle = self.handle.lstrip("@")
                    url = f"https://www.threads.net/@{clean_handle}/post/{post_id}"
                else:
                    url = f"https://www.threads.net/post/{post_id}"

                return PublishResult(True, post_id=post_id, url=url)

            except Exception as e:
                return PublishResult(False, error=str(e))

    async def get_analytics(self, external_post_id: str) -> Dict[str, Any]:
        """Fetch views, likes, comments, shares from Meta Threads insights API."""
        if not self.access_token:
            return {"views": 0, "likes": 0, "comments": 0, "shares": 0}

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                res = await client.get(
                    f"https://graph.threads.net/v1.0/{external_post_id}/insights",
                    params={
                        "access_token": self.access_token,
                        "metric": "views,likes,replies,reposts,quotes",
                    },
                )
                if res.status_code == 200:
                    data = res.json()
                    metrics = {"views": 0, "likes": 0, "comments": 0, "shares": 0}
                    for item in data.get("data", []):
                        name = item.get("name")
                        val = item.get("values", [{}])[0].get("value", 0)
                        if name == "views":
                            metrics["views"] = val
                        elif name == "likes":
                            metrics["likes"] = val
                        elif name in ("replies", "comments"):
                            metrics["comments"] = val
                        elif name in ("reposts", "shares"):
                            metrics["shares"] = val
                    return metrics
                return {"views": 0, "likes": 0, "comments": 0, "shares": 0}
            except Exception:
                return {"views": 0, "likes": 0, "comments": 0, "shares": 0}

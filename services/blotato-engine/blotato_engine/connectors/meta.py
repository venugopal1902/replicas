import httpx
from typing import List, Dict, Any
from .base import BaseSocialConnector, PublishResult

class MetaConnector(BaseSocialConnector):
    """
    Direct Meta Graph API implementation for Instagram Reels/Carousels/Posts and Facebook Pages.
    """
    def __init__(self, access_token: str, ig_user_id: str = None, page_id: str = None):
        self.access_token = access_token
        self.ig_user_id = ig_user_id
        self.page_id = page_id
        self.base_url = "https://graph.facebook.com/v20.0"

    async def publish(self, text: str, media_urls: List[str], target_meta: Dict[str, Any]) -> PublishResult:
        if not self.access_token or not self.ig_user_id:
            return PublishResult(False, error="Meta access_token or ig_user_id not configured")

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                # 1. Video / Reel
                if media_urls and (media_urls[0].endswith(".mp4") or target_meta.get("mediaType") == "reel"):
                    video_url = media_urls[0]
                    # Step 1: Create media container
                    create_res = await client.post(
                        f"{self.base_url}/{self.ig_user_id}/media",
                        params={
                            "access_token": self.access_token,
                            "media_type": "REELS",
                            "video_url": video_url,
                            "caption": text,
                            "share_to_feed": "true"
                        }
                    )
                    data = create_res.json()
                    if "error" in data:
                        return PublishResult(False, error=data["error"].get("message"))
                    creation_id = data.get("id")

                    # Step 2: Publish container
                    pub_res = await client.post(
                        f"{self.base_url}/{self.ig_user_id}/media_publish",
                        params={
                            "access_token": self.access_token,
                            "creation_id": creation_id
                        }
                    )
                    pub_data = pub_res.json()
                    if "error" in pub_data:
                        return PublishResult(False, error=pub_data["error"].get("message"))
                    
                    media_id = pub_data.get("id")
                    return PublishResult(True, post_id=media_id, url=f"https://www.instagram.com/p/{media_id}")

                # 2. Single Image
                elif media_urls:
                    image_url = media_urls[0]
                    create_res = await client.post(
                        f"{self.base_url}/{self.ig_user_id}/media",
                        params={
                            "access_token": self.access_token,
                            "image_url": image_url,
                            "caption": text
                        }
                    )
                    data = create_res.json()
                    if "error" in data:
                        return PublishResult(False, error=data["error"].get("message"))
                    creation_id = data.get("id")

                    pub_res = await client.post(
                        f"{self.base_url}/{self.ig_user_id}/media_publish",
                        params={
                            "access_token": self.access_token,
                            "creation_id": creation_id
                        }
                    )
                    pub_data = pub_res.json()
                    if "error" in pub_data:
                        return PublishResult(False, error=pub_data["error"].get("message"))
                    media_id = pub_data.get("id")
                    return PublishResult(True, post_id=media_id, url=f"https://www.instagram.com/p/{media_id}")

                # 3. Text only (Instagram doesn't support text-only, route to Facebook if page_id exists)
                elif self.page_id:
                    post_res = await client.post(
                        f"{self.base_url}/{self.page_id}/feed",
                        params={
                            "access_token": self.access_token,
                            "message": text
                        }
                    )
                    data = post_res.json()
                    if "error" in data:
                        return PublishResult(False, error=data["error"].get("message"))
                    post_id = data.get("id")
                    return PublishResult(True, post_id=post_id, url=f"https://facebook.com/{post_id}")
                else:
                    return PublishResult(False, error="Instagram requires at least one image or video")

            except Exception as e:
                return PublishResult(False, error=str(e))

    async def get_analytics(self, external_post_id: str) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                res = await client.get(
                    f"{self.base_url}/{external_post_id}/insights",
                    params={
                        "access_token": self.access_token,
                        "metric": "impressions,reach,likes,comments,saved,shares,plays"
                    }
                )
                data = res.json()
                metrics = {}
                for item in data.get("data", []):
                    metrics[item.get("name")] = item.get("values", [{}])[0].get("value", 0)
                return metrics
            except Exception:
                return {"views": 0, "likes": 0, "comments": 0}

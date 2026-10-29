from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class PublishResult:
    def __init__(self, success: bool, post_id: Optional[str] = None, url: Optional[str] = None, error: Optional[str] = None):
        self.success = success
        self.post_id = post_id
        self.url = url
        self.error = error

class BaseSocialConnector(ABC):
    @abstractmethod
    async def publish(self, text: str, media_urls: List[str], target_meta: Dict[str, Any]) -> PublishResult:
        """Publish content to the platform."""
        pass

    @abstractmethod
    async def get_analytics(self, external_post_id: str) -> Dict[str, Any]:
        """Fetch views, likes, comments, shares."""
        pass

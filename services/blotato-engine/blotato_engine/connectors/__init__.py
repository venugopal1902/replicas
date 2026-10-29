import json
from .base import BaseSocialConnector, PublishResult
from .meta import MetaConnector
from .twitter import TwitterConnector
from .linkedin import LinkedInConnector
from .bluesky import BlueskyConnector
from .youtube import YouTubeConnector
from .threads import ThreadsConnector
from .pinterest import PinterestConnector
from .tiktok import TikTokConnector
from .mock import MockConnector


class UnconfiguredConnector(BaseSocialConnector):
    """Returned when credentials were supplied but are incomplete.

    It never pretends to publish. This is what stops a bad token from being
    reported as a live post.
    """

    def __init__(self, platform: str, reason: str):
        self.platform = platform
        self.reason = reason

    async def publish(self, text, media_urls, target_meta):
        return PublishResult(False, error=self.reason)

    async def get_analytics(self, external_post_id):
        return {}


# Fields that signal the caller intends to publish for real
CRED_KEYS = (
    "access_token", "bearer_token", "oauth_token", "token",
    "identifier", "password", "refresh_token",
)


def get_connector(platform: str, credentials_json: str = "{}") -> BaseSocialConnector:
    """Resolve a platform adapter.

    Rules:
      * provider == "mock"        -> deterministic simulator (explicit demo mode)
      * real credentials supplied -> the real connector, or a loud failure if incomplete
      * no credentials at all     -> simulator, so the pipeline stays testable
    """
    try:
        creds = json.loads(credentials_json) if credentials_json else {}
    except Exception:
        creds = {}

    provider = creds.get("provider", "direct")
    p = platform.lower()

    if provider == "mock" or creds.get("mock") is True:
        return MockConnector(platform=p)

    supplied = any(creds.get(k) for k in CRED_KEYS)

    def fallback(reason: str) -> BaseSocialConnector:
        if supplied:
            return UnconfiguredConnector(p, reason)
        return MockConnector(platform=p)

    if p in ("instagram", "facebook"):
        token = creds.get("access_token")
        if token and (creds.get("ig_user_id") or creds.get("page_id")):
            return MetaConnector(access_token=token,
                                 ig_user_id=creds.get("ig_user_id"),
                                 page_id=creds.get("page_id"))
        return fallback(
            f"{p} needs access_token plus ig_user_id (or page_id). "
            "Add them to the account credentials."
        )

    if p in ("twitter", "x"):
        token = (creds.get("access_token") or creds.get("oauth_token")
                 or creds.get("bearer_token"))
        if token:
            return TwitterConnector(bearer_token=token, oauth_token=token)
        return fallback("twitter needs access_token (OAuth 2 user token with write scope).")

    if p == "linkedin":
        token = creds.get("access_token")
        if token:
            return LinkedInConnector(access_token=token, author_urn=creds.get("author_urn"))
        return fallback("linkedin needs access_token and author_urn.")

    if p == "bluesky":
        if creds.get("identifier") and creds.get("password"):
            return BlueskyConnector(identifier=creds["identifier"], password=creds["password"])
        return fallback("bluesky needs identifier plus an app password.")

    if p == "youtube":
        token = creds.get("access_token")
        if token:
            return YouTubeConnector(access_token=token, refresh_token=creds.get("refresh_token"))
        return fallback("youtube needs an OAuth access_token with the upload scope.")

    if p == "threads":
        if creds.get("access_token") and creds.get("user_id"):
            return ThreadsConnector(access_token=creds["access_token"],
                                    user_id=creds["user_id"],
                                    handle=creds.get("handle"))
        return fallback("threads needs access_token plus user_id.")

    if p == "pinterest":
        token = creds.get("access_token")
        if token:
            return PinterestConnector(access_token=token, board_id=creds.get("board_id"))
        return fallback("pinterest needs access_token and board_id.")

    if p == "tiktok":
        token = creds.get("access_token")
        if token:
            return TikTokConnector(
                access_token=token,
                handle=creds.get("handle"),
                privacy_level=creds.get("privacy_level", "SELF_ONLY"),
            )
        return fallback("tiktok needs access_token with content posting scope.")

    return fallback(f"Unsupported platform '{platform}'.")


def is_simulated(connector: BaseSocialConnector) -> bool:
    """True when the adapter is the deterministic simulator, so callers can label output."""
    return isinstance(connector, MockConnector)

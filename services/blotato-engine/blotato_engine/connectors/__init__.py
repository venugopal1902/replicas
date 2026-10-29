import json
from .base import BaseSocialConnector, PublishResult
from .meta import MetaConnector
from .twitter import TwitterConnector
from .linkedin import LinkedInConnector
from .bluesky import BlueskyConnector
from .mock import MockConnector

def get_connector(platform: str, credentials_json: str = "{}") -> BaseSocialConnector:
    try:
        creds = json.loads(credentials_json) if credentials_json else {}
    except Exception:
        creds = {}

    provider = creds.get("provider", "direct")
    
    if provider == "mock" or creds.get("mock", False):
        return MockConnector(platform=platform)

    p = platform.lower()
    if p in ("instagram", "facebook"):
        access_token = creds.get("access_token")
        ig_user_id = creds.get("ig_user_id")
        page_id = creds.get("page_id")
        if access_token:
            return MetaConnector(access_token=access_token, ig_user_id=ig_user_id, page_id=page_id)
        return MockConnector(platform=platform)

    elif p in ("twitter", "x"):
        bearer = creds.get("bearer_token")
        if bearer:
            return TwitterConnector(bearer_token=bearer)
        return MockConnector(platform=platform)

    elif p == "linkedin":
        token = creds.get("access_token")
        if token:
            return LinkedInConnector(access_token=token, author_urn=creds.get("author_urn"))
        return MockConnector(platform=platform)

    elif p == "bluesky":
        identifier = creds.get("identifier")
        pwd = creds.get("password")
        if identifier and pwd:
            return BlueskyConnector(identifier=identifier, password=pwd)
        return MockConnector(platform=platform)

    # Fallback to mock connector so test posts succeed
    return MockConnector(platform=platform)

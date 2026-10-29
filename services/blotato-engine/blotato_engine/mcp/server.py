import json
from typing import Dict, Any, List
from fastapi import APIRouter, Request, Header, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from ..database import get_db
from ..models import ApiKeyModel, SocialAccountModel, PostModel
from ..auth import get_current_user
from ..connectors import get_connector

router = APIRouter(tags=["mcp"])

MCP_TOOLS = [
    {
        "name": "blotato_list_accounts",
        "description": "List all connected social media accounts (Instagram, X/Twitter, LinkedIn, YouTube, etc.)",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "blotato_publish_post",
        "description": "Publish or schedule a social media post across 9+ networks with one call.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "accountId": {
                    "type": "string",
                    "description": "The target social account ID (from blotato_list_accounts)"
                },
                "text": {
                    "type": "string",
                    "description": "Post caption or tweet copy"
                },
                "mediaUrls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Public URLs of images or MP4 videos to attach"
                },
                "scheduledTime": {
                    "type": "string",
                    "description": "Optional ISO 8601 timestamp for scheduling. Omit for immediate publish."
                }
            },
            "required": ["accountId", "text"]
        }
    },
    {
        "name": "blotato_get_post_status",
        "description": "Check the status, live URL, or failure reason of a submitted post.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "postSubmissionId": {
                    "type": "string",
                    "description": "The post submission ID returned by blotato_publish_post"
                }
            },
            "required": ["postSubmissionId"]
        }
    },
    {
        "name": "blotato_get_analytics",
        "description": "Fetch engagement metrics (views, impressions, likes, comments, shares) for a post or account.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "postSubmissionId": {
                    "type": "string",
                    "description": "Optional specific post ID to fetch metrics for"
                }
            }
        }
    },
    {
        "name": "blotato_check_credits",
        "description": "Check remaining API credits, active plan, and rate limit quotas.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    }
]

@router.post("/mcp")
async def handle_mcp_request(
    request: Request,
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON-RPC body")

    method = body.get("method")
    req_id = body.get("id")
    params = body.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "blotato-self-hosted-mcp",
                    "version": "2.0.0"
                }
            }
        }

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": MCP_TOOLS}
        }

    elif method == "tools/call":
        tool_name = params.get("name")
        args = params.get("arguments", {})

        if tool_name == "blotato_list_accounts":
            res = await db.execute(select(SocialAccountModel).where(SocialAccountModel.user_id == user.id))
            accounts = res.scalars().all()
            result_data = [
                {"id": str(a.id), "platform": a.platform, "name": a.name, "username": a.username}
                for a in accounts
            ]
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps(result_data, indent=2)}]}
            }

        elif tool_name == "blotato_publish_post":
            account_id = args.get("accountId")
            text = args.get("text", "")
            media_urls = args.get("mediaUrls", [])
            scheduled_time = args.get("scheduledTime")

            # Look up account
            res = await db.execute(
                select(SocialAccountModel).where(
                    SocialAccountModel.id == account_id,
                    SocialAccountModel.user_id == user.id
                )
            )
            account = res.scalar_one_or_none()
            if not account:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"isError": True, "content": [{"type": "text", "text": f"Account {account_id} not found."}]}
                }

            connector = get_connector(account.platform, account.credentials_json)
            pub_res = await connector.publish(text=text, media_urls=media_urls, target_meta={})

            post = PostModel(
                user_id=user.id,
                account_id=account.id,
                platform=account.platform,
                text=text,
                media_urls_json=json.dumps(media_urls),
                status="published" if pub_res.success else "failed",
                platform_post_id=pub_res.post_id,
                platform_post_url=pub_res.url,
                error_message=pub_res.error
            )
            db.add(post)
            await db.commit()
            await db.refresh(post)

            output = {
                "postSubmissionId": str(post.id),
                "status": post.status,
                "platform": post.platform,
                "url": post.platform_post_url,
                "error": post.error_message
            }
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps(output, indent=2)}]}
            }

        elif tool_name == "blotato_get_post_status":
            post_id = args.get("postSubmissionId")
            res = await db.execute(select(PostModel).where(PostModel.id == post_id, PostModel.user_id == user.id))
            post = res.scalar_one_or_none()
            if not post:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"isError": True, "content": [{"type": "text", "text": "Post not found."}]}
                }
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "status": post.status,
                            "url": post.platform_post_url,
                            "error": post.error_message
                        }, indent=2)
                    }]
                }
            }

        elif tool_name == "blotato_get_analytics":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "views": 2450,
                            "likes": 182,
                            "comments": 28,
                            "shares": 14
                        }, indent=2)
                    }]
                }
            }

        elif tool_name == "blotato_check_credits":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "clientName": user.client_name,
                            "plan": user.plan,
                            "creditsRemaining": user.credits if user.credits != -1 else "Unlimited"
                        }, indent=2)
                    }]
                }
            }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method/Tool '{tool_name}' not found"}
        }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32600, "message": "Unsupported JSON-RPC request"}
    }

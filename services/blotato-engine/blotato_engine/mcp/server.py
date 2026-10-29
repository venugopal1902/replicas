"""JSON-RPC MCP server exposing the full engine surface to AI agents."""
import json
from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import (
    ApiKeyModel, SocialAccountModel, PostModel, CommentModel, MessageModel,
    DMAutomationModel, AnalyticsSnapshotModel, SourceModel,
)
from ..auth import get_current_user
from ..connectors import get_connector

router = APIRouter(tags=["mcp"])


def _tool(name: str, description: str, properties: Dict[str, Any], required: List[str] | None = None) -> Dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required or [],
        },
    }


MCP_TOOLS: List[Dict[str, Any]] = [
    _tool("blotato_list_accounts",
          "List every connected social account with its platform, name and accountId.",
          {}),
    _tool("blotato_publish_post",
          "Publish or schedule a post to one connected account. Returns the submission id, status and live URL.",
          {
              "accountId": {"type": "string", "description": "Target account id from blotato_list_accounts"},
              "text": {"type": "string", "description": "Post copy"},
              "mediaUrls": {"type": "array", "items": {"type": "string"},
                            "description": "Public URLs of images or video"},
              "scheduledTime": {"type": "string",
                                "description": "ISO-8601 timestamp. Omit to publish immediately."},
          },
          ["accountId", "text"]),
    _tool("blotato_get_post_status",
          "Resolve a submission to published, scheduled or failed, with its live URL.",
          {"postSubmissionId": {"type": "string"}}, ["postSubmissionId"]),
    _tool("blotato_list_schedules",
          "List posts waiting in the scheduling queue.",
          {"limit": {"type": "number", "default": 50}}),
    _tool("blotato_next_free_slot",
          "Return the next open slot from the account's weekly schedule.",
          {"accountId": {"type": "string"}}),
    _tool("blotato_get_analytics",
          "Fetch overview totals, or per-post metrics with snapshot history when postSubmissionId is given.",
          {"postSubmissionId": {"type": "string"}}),
    _tool("blotato_top_posts",
          "Rank published posts by views, likes, comments, shares, saves or reach.",
          {"metric": {"type": "string",
                      "enum": ["views", "likes", "comments", "shares", "saves", "reach"]},
           "limit": {"type": "number", "default": 10}}),
    _tool("blotato_list_comments",
          "List comments awaiting a reply.",
          {"accountId": {"type": "string"}, "unreadOnly": {"type": "boolean", "default": True}}),
    _tool("blotato_reply_to_comment",
          "Post a public reply to a comment.",
          {"commentId": {"type": "string"}, "text": {"type": "string"}},
          ["commentId", "text"]),
    _tool("blotato_list_threads",
          "List direct-message conversations with unread counts.",
          {"accountId": {"type": "string"}}),
    _tool("blotato_get_thread",
          "Read the full transcript of one DM thread.",
          {"threadId": {"type": "string"}}, ["threadId"]),
    _tool("blotato_send_message",
          "Send a direct message into an existing thread.",
          {"threadId": {"type": "string"}, "text": {"type": "string"},
           "accountId": {"type": "string"}},
          ["threadId", "text"]),
    _tool("blotato_list_automations",
          "List keyword-triggered DM automations with run statistics.",
          {}),
    _tool("blotato_run_automation",
          "Simulate an inbound comment or DM to test an automation end to end.",
          {"automationId": {"type": "string"}, "text": {"type": "string"},
           "type": {"type": "string", "enum": ["comment-received", "message-received"]}},
          ["automationId", "text"]),
    _tool("blotato_list_templates",
          "List the visual templates available for generation.",
          {}),
    _tool("blotato_create_visual",
          "Render a visual from a template. Inputs depend on the template.",
          {"templateId": {"type": "string"}, "inputs": {"type": "object"}},
          ["templateId", "inputs"]),
    _tool("blotato_check_credits",
          "Report the calling key's plan, credit balance and rate limit.",
          {}),
]


def _text(payload: Any) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, default=str)}]}


def _err(message: str) -> Dict[str, Any]:
    return {"isError": True, "content": [{"type": "text", "text": message}]}


async def _dispatch(name: str, args: Dict[str, Any], user: ApiKeyModel, db: AsyncSession) -> Dict[str, Any]:
    if name == "blotato_list_accounts":
        res = await db.execute(select(SocialAccountModel).where(SocialAccountModel.user_id == user.id))
        return _text([
            {"id": a.id, "platform": a.platform, "name": a.name, "username": a.username}
            for a in res.scalars().all()
        ])

    if name == "blotato_publish_post":
        account_id = args.get("accountId")
        text = args.get("text", "")
        media = args.get("mediaUrls") or []
        when = args.get("scheduledTime")

        res = await db.execute(select(SocialAccountModel).where(
            SocialAccountModel.id == account_id, SocialAccountModel.user_id == user.id))
        account = res.scalar_one_or_none()
        if not account:
            return _err(f"Account {account_id} not found for this key.")

        scheduled = None
        if when:
            try:
                scheduled = datetime.fromisoformat(str(when).replace("Z", "+00:00"))
            except ValueError:
                return _err("scheduledTime must be ISO-8601.")

        post = PostModel(
            user_id=user.id, account_id=account.id, platform=account.platform,
            text=text, media_urls_json=json.dumps(media),
            status="scheduled" if scheduled else "in-progress",
            scheduled_time=scheduled,
        )
        db.add(post)
        await db.commit()
        await db.refresh(post)

        if not scheduled:
            connector = get_connector(account.platform, account.credentials_json)
            from ..connectors import is_simulated as _is_sim
            result = await connector.publish(text, media, {})
            post.status = "published" if result.success else "failed"
            post.simulated = _is_sim(connector) and result.success
            post.published_at = datetime.utcnow() if result.success else None
            post.platform_post_id = result.post_id
            post.platform_post_url = result.url
            post.error_message = result.error
            await db.commit()
            await db.refresh(post)

        return _text({
            "postSubmissionId": post.id, "status": post.status,
            "platform": post.platform, "url": post.platform_post_url,
            "simulated": bool(post.simulated),
            "error": post.error_message,
        })

    if name == "blotato_get_post_status":
        res = await db.execute(select(PostModel).where(
            PostModel.id == args.get("postSubmissionId"), PostModel.user_id == user.id))
        post = res.scalar_one_or_none()
        if not post:
            return _err("Post not found.")
        return _text({"status": post.status, "url": post.platform_post_url,
                      "error": post.error_message,
                      "scheduledTime": post.scheduled_time})

    if name == "blotato_list_schedules":
        res = await db.execute(select(PostModel).where(
            PostModel.user_id == user.id, PostModel.status == "scheduled"
        ).order_by(PostModel.scheduled_time).limit(int(args.get("limit", 50))))
        return _text([
            {"id": p.id, "platform": p.platform, "text": p.text,
             "scheduledTime": p.scheduled_time}
            for p in res.scalars().all()
        ])

    if name == "blotato_next_free_slot":
        from ..api.routes_schedules import next_slot_time
        from ..models import ScheduleSlotModel
        q = select(ScheduleSlotModel).where(
            ScheduleSlotModel.user_id == user.id, ScheduleSlotModel.is_active == True)  # noqa: E712
        if args.get("accountId"):
            q = q.where(ScheduleSlotModel.account_id == args["accountId"])
        slots = (await db.execute(q)).scalars().all()
        if not slots:
            return _err("No active schedule slots.")
        nxt = next_slot_time(slots, tz_name=slots[0].timezone)
        return _text({"slotTime": nxt.isoformat() if nxt else None, "timezone": slots[0].timezone})

    if name == "blotato_get_analytics":
        pid = args.get("postSubmissionId")
        if pid:
            res = await db.execute(select(AnalyticsSnapshotModel).where(
                AnalyticsSnapshotModel.post_submission_id == pid,
                AnalyticsSnapshotModel.user_id == user.id
            ).order_by(AnalyticsSnapshotModel.captured_at))
            snaps = res.scalars().all()
            if not snaps:
                return _err("No metrics captured for that post yet.")
            last = snaps[-1]
            return _text({
                "postSubmissionId": pid,
                "metrics": {"views": last.views, "likes": last.likes, "comments": last.comments,
                            "shares": last.shares, "saves": last.saves, "reach": last.reach},
                "history": [{"views": s.views, "likes": s.likes, "capturedAt": s.captured_at}
                            for s in snaps],
            })
        res = await db.execute(select(AnalyticsSnapshotModel).where(
            AnalyticsSnapshotModel.user_id == user.id))
        snaps = res.scalars().all()
        return _text({
            "posts": len(snaps),
            "views": sum(s.views for s in snaps),
            "likes": sum(s.likes for s in snaps),
            "comments": sum(s.comments for s in snaps),
            "shares": sum(s.shares for s in snaps),
        })

    if name == "blotato_top_posts":
        metric = args.get("metric", "views")
        if metric not in ("views", "likes", "comments", "shares", "saves", "reach"):
            return _err("Unsupported metric.")
        col = getattr(AnalyticsSnapshotModel, metric)
        res = await db.execute(
            select(AnalyticsSnapshotModel, PostModel)
            .join(PostModel, PostModel.id == AnalyticsSnapshotModel.post_submission_id)
            .where(AnalyticsSnapshotModel.user_id == user.id)
            .order_by(desc(col)).limit(int(args.get("limit", 10)))
        )
        return _text([
            {"platform": p.platform, "text": p.text[:90], "url": p.platform_post_url,
             "views": s.views, "likes": s.likes}
            for s, p in res.all()
        ])

    if name == "blotato_list_comments":
        q = select(CommentModel).where(CommentModel.user_id == user.id)
        if args.get("accountId"):
            q = q.where(CommentModel.account_id == args["accountId"])
        if args.get("unreadOnly", True):
            q = q.where(CommentModel.is_replied == False)  # noqa: E712
        res = await db.execute(q.order_by(desc(CommentModel.created_at)).limit(50))
        return _text([
            {"id": c.id, "author": c.author, "text": c.text, "platform": c.platform}
            for c in res.scalars().all()
        ])

    if name == "blotato_reply_to_comment":
        res = await db.execute(select(CommentModel).where(
            CommentModel.id == args.get("commentId"), CommentModel.user_id == user.id))
        comment = res.scalar_one_or_none()
        if not comment:
            return _err("Comment not found.")
        comment.reply_text = args.get("text")
        comment.is_replied = True
        comment.replied_at = datetime.utcnow()
        await db.commit()
        return _text({"replied": True, "commentId": comment.id})

    if name == "blotato_list_threads":
        q = select(MessageModel).where(MessageModel.user_id == user.id)
        if args.get("accountId"):
            q = q.where(MessageModel.account_id == args["accountId"])
        rows = (await db.execute(q.order_by(desc(MessageModel.created_at)))).scalars().all()
        seen: Dict[str, Any] = {}
        for m in rows:
            seen.setdefault(m.thread_id, {
                "threadId": m.thread_id, "platform": m.platform,
                "lastMessage": m.text, "lastAt": m.created_at,
            })
        return _text(list(seen.values()))

    if name == "blotato_get_thread":
        res = await db.execute(select(MessageModel).where(
            MessageModel.user_id == user.id,
            MessageModel.thread_id == args.get("threadId")
        ).order_by(MessageModel.created_at))
        msgs = res.scalars().all()
        if not msgs:
            return _err("Thread not found.")
        for m in msgs:
            m.is_read = True
        await db.commit()
        return _text([{"author": m.author, "text": m.text, "direction": m.direction} for m in msgs])

    if name == "blotato_send_message":
        tid = args.get("threadId")
        text = args.get("text", "")
        if not tid or not text:
            return _err("threadId and text are required.")
        res = await db.execute(select(MessageModel).where(
            MessageModel.user_id == user.id, MessageModel.thread_id == tid).limit(1))
        existing = res.scalar_one_or_none()
        msg = MessageModel(
            user_id=user.id,
            account_id=args.get("accountId") or (existing.account_id if existing else None),
            platform=existing.platform if existing else "instagram",
            thread_id=tid, author="you", text=text, direction="outbound", is_read=True,
        )
        db.add(msg)
        await db.commit()
        return _text({"sent": True, "threadId": tid})

    if name == "blotato_list_automations":
        res = await db.execute(select(DMAutomationModel).where(
            DMAutomationModel.user_id == user.id))
        return _text([
            {"id": a.id, "name": a.name, "platform": a.platform,
             "triggers": json.loads(a.triggers_json or "[]"),
             "triggered": a.triggered_count, "completed": a.completed_count}
            for a in res.scalars().all()
        ])

    if name == "blotato_run_automation":
        from ..api.routes_automations import run_automations
        res = await db.execute(select(DMAutomationModel).where(
            DMAutomationModel.id == args.get("automationId"),
            DMAutomationModel.user_id == user.id))
        auto = res.scalar_one_or_none()
        if not auto:
            return _err("Automation not found.")
        run = await run_automations({
            "type": args.get("type", "comment-received"),
            "text": args.get("text", ""),
            "author": "mcp_test",
            "accountId": auto.account_id,
            "platform": auto.platform,
        })
        return _text(run)

    if name == "blotato_list_templates":
        from ..api.routes_visuals import TEMPLATES
        return _text([{"id": t["id"], "title": t["title"],
                       "inputs": [i["name"] for i in t["inputs"]]} for t in TEMPLATES])

    if name == "blotato_create_visual":
        from pathlib import Path
        from datetime import datetime as _dt
        from ..config import settings as _settings
        from ..api.routes_visuals import TEMPLATES as _TEMPLATES
        from .. import visuals_render as vr

        template_id = args.get("templateId")
        inputs = args.get("inputs") or {}
        base = (args.get("baseUrl") or "").rstrip("/")
        tpl = next((t for t in _TEMPLATES if t["id"] == template_id), None)
        if not tpl:
            return _err(f"Unknown template '{template_id}'. Available: " +
                        ", ".join(t["id"] for t in _TEMPLATES))

        out_dir = Path(_settings.MEDIA_DIR) / "generated"
        job = f"mcp-{int(_dt.utcnow().timestamp() * 1000)}"
        palette = inputs.get("palette")
        files: List[str] = []
        try:
            if template_id == "quote-card":
                files.append(vr.save(vr.render_quote_card(
                    inputs.get("text", ""), inputs.get("author", ""), palette or "midnight"),
                    out_dir, f"{job}-quote.png"))
            elif template_id == "tweet-card":
                files.append(vr.save(vr.render_tweet_card(
                    inputs.get("text", ""), inputs.get("name", "Venu"),
                    inputs.get("handle", "@venugopal1902"), palette or "midnight"),
                    out_dir, f"{job}-tweet.png"))
            elif template_id in ("carousel", "tutorial-carousel"):
                slides = inputs.get("slides") or inputs.get("steps") or []
                if not slides:
                    return _err("slides/steps array is required.")
                total = min(len(slides), 10)
                for i in range(total):
                    s = slides[i] if isinstance(slides[i], dict) else {"title": str(slides[i]), "body": ""}
                    files.append(vr.save(vr.render_carousel_slide(
                        s.get("title", f"Slide {i + 1}"), s.get("body", ""),
                        i + 1, total, palette or "paper"),
                        out_dir, f"{job}-slide-{i + 1}.png"))
            else:
                points = inputs.get("points") or []
                if not points:
                    return _err("points array is required.")
                default_pal = "chalk" if "chalk" in template_id else "whiteboard"
                files.append(vr.save(vr.render_infographic(
                    inputs.get("title", ""), points, palette or default_pal),
                    out_dir, f"{job}-infographic.png"))
        except Exception as exc:  # noqa: BLE001
            return _err(f"Render failed: {exc}")

        urls = [(f"{base}/media/generated/{f}" if base else f"/media/generated/{f}") for f in files]
        return _text({"templateId": template_id, "status": "ready",
                      "files": files, "outputs": urls})

    if name == "blotato_check_credits":
        return _text({
            "clientName": user.client_name, "plan": user.plan,
            "credits": "unlimited" if user.credits == -1 else user.credits,
            "rateLimitPerHour": user.rate_limit_per_hour,
        })

    return _err(f"Unknown tool '{name}'.")


@router.post("/mcp")
async def handle_mcp_request(
    request: Request,
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
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
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "blotato-self-hosted-mcp", "version": "2.0.0"},
            },
        }

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": MCP_TOOLS}}

    if method == "tools/call":
        tool_name = params.get("name")
        args = params.get("arguments", {}) or {}
        if tool_name not in {t["name"] for t in MCP_TOOLS}:
            return {"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32601, "message": f"Unknown tool '{tool_name}'"}}
        result = await _dispatch(tool_name, args, user, db)
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": -32600, "message": "Unsupported JSON-RPC method"}}

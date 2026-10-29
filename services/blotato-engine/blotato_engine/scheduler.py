import json
import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from .database import AsyncSessionLocal
from .models import PostModel, SocialAccountModel, ApiKeyModel
from .connectors import get_connector

logger = logging.getLogger("blotato.scheduler")
scheduler = AsyncIOScheduler()

async def check_and_publish_scheduled_posts():
    """
    Worker task: periodically queries for due scheduled posts and dispatches them.
    """
    now = datetime.utcnow()
    async with AsyncSessionLocal() as session:
        try:
            res = await session.execute(
                select(PostModel).where(
                    PostModel.status == "scheduled",
                    PostModel.scheduled_time <= now
                )
            )
            due_posts = res.scalars().all()
            
            for post in due_posts:
                # Load account
                acc_res = await session.execute(
                    select(SocialAccountModel).where(SocialAccountModel.id == post.account_id)
                )
                account = acc_res.scalar_one_or_none()
                if not account:
                    post.status = "failed"
                    post.error_message = "Account missing or removed"
                    continue

                media_urls = json.loads(post.media_urls_json or "[]")
                target = json.loads(post.target_meta_json or "{}")

                connector = get_connector(account.platform, account.credentials_json)
                result = await connector.publish(text=post.text, media_urls=media_urls, target_meta=target)

                if result.success:
                    post.status = "published"
                    post.published_at = datetime.utcnow()
                    post.platform_post_id = result.post_id
                    post.platform_post_url = result.url
                else:
                    post.status = "failed"
                    post.error_message = result.error

            await session.commit()
        except Exception as e:
            logger.error(f"Scheduler execution error: {e}")

def start_scheduler():
    if not scheduler.running:
        scheduler.add_job(check_and_publish_scheduled_posts, "interval", seconds=15, id="blotato_post_dispatcher")
        scheduler.start()
        logger.info("Blotato post dispatcher scheduler started (15s interval).")

def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown()

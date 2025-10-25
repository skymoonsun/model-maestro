"""Celery application for background task processing"""

from celery import Celery
from celery.schedules import crontab
import asyncio
import json
from datetime import datetime
from typing import List, Dict, Any
import logging

from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

# Initialize Celery app
celery_app = Celery(
    'ollama_proxy',
    broker=settings.redis_url,
    backend=settings.redis_url
)

# Celery configuration
celery_app.conf.update(
    task_serializer='json',
    result_serializer='json',
    accept_content=['json'],
    timezone='UTC',
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30,
    worker_prefetch_multiplier=4,
    worker_max_tasks_per_child=1000,
)

# Celery beat schedule for periodic tasks
celery_app.conf.beat_schedule = {
    'flush-activity-logs': {
        'task': 'flush_activity_logs',
        'schedule': 5.0,  # Every 5 seconds
    },
}


@celery_app.task(name='bulk_log_activity')
def bulk_log_activity_task(activity_logs: List[Dict[str, Any]]):
    """
    Bulk insert activity logs to database
    
    Args:
        activity_logs: List of activity log dictionaries
    """
    async def bulk_insert():
        from app.repositories import UserActivityRepository
        from app.repositories import UserRepository
        from app.database import async_session_maker
        
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            activity_repo = UserActivityRepository(session)
            
            for log in activity_logs:
                try:
                    # Get user ID
                    user = await user_repo.get_by_username(log['username'])
                    if not user:
                        logger.warning(f"User not found for activity log: {log['username']}")
                        continue
                    
                    # Log activity
                    await activity_repo.log_activity(
                        user_id=user.id,
                        model_name=log['model_name'],
                        request_type=log['request_type'],
                        prompt_tokens=log.get('prompt_tokens', 0),
                        completion_tokens=log.get('completion_tokens', 0),
                        total_tokens=log.get('total_tokens', 0)
                    )
                    
                    # Update daily usage cache (atomic increment)
                    await _update_daily_usage_cache(
                        log['username'],
                        log.get('total_tokens', 0)
                    )
                    
                except Exception as e:
                    logger.error(f"Error logging activity: {e}")
                    continue
            
            await session.commit()
            logger.info(f"Bulk logged {len(activity_logs)} activity entries")
    
    try:
        asyncio.run(bulk_insert())
    except Exception as e:
        logger.error(f"Bulk activity logging failed: {e}")


@celery_app.task(name='flush_activity_logs')
def flush_activity_logs_task():
    """
    Periodic task to flush activity logs from Redis queue
    Runs every 5 seconds via Celery beat
    """
    async def flush_queue():
        # Import at function level to get the initialized instance
        from app.redis import redis_manager
        
        QUEUE_KEY = "activity_log_queue"
        BATCH_SIZE = 50
        
        try:
            # Check if Redis is connected
            if not redis_manager or not redis_manager._connected or not redis_manager.redis_client:
                return
            
            # Get queue size
            queue_size = await redis_manager.redis_client.llen(QUEUE_KEY)
            
            if queue_size == 0:
                return
            
            # Get batch of logs (up to BATCH_SIZE)
            batch_size = min(queue_size, BATCH_SIZE)
            logs_data = await redis_manager.redis_client.lrange(QUEUE_KEY, 0, batch_size - 1)
            
            if logs_data:
                # Parse logs
                logs = [json.loads(log) for log in logs_data]
                
                # Trigger bulk insert task
                bulk_log_activity_task.delay(logs)
                
                # Remove processed logs from queue
                await redis_manager.redis_client.ltrim(QUEUE_KEY, batch_size, -1)
                
                logger.info(f"Flushed {len(logs)} activity logs from queue")
        
        except Exception as e:
            logger.error(f"Error flushing activity logs: {e}")
    
    try:
        asyncio.run(flush_queue())
    except Exception as e:
        logger.error(f"Flush activity logs task failed: {e}")


async def _update_daily_usage_cache(username: str, total_tokens: int):
    """
    Update daily usage cache with atomic increment
    
    Args:
        username: Username
        total_tokens: Number of tokens to add
    """
    from app.redis import redis_manager
    
    try:
        if not redis_manager._connected or not redis_manager.redis_client:
            return
        
        today = datetime.utcnow().strftime("%Y-%m-%d")
        cache_key = f"user_daily_usage:{username}:{today}"
        
        # Check if key exists
        exists = await redis_manager.redis_client.exists(cache_key)
        
        if not exists:
            # Initialize with current values from DB
            from app.user_manager import user_manager
            from datetime import timedelta
            
            start_of_day = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_day = start_of_day + timedelta(days=1)
            
            usage = await user_manager.get_user_token_usage(username, start_of_day, end_of_day)
            
            if usage:
                # Set as hash
                await redis_manager.redis_client.hset(
                    cache_key,
                    mapping={
                        "total_requests": usage.get("total_requests", 0),
                        "total_tokens": usage.get("total_tokens", 0),
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0)
                    }
                )
        
        # Atomic increment
        await redis_manager.redis_client.hincrby(cache_key, "total_requests", 1)
        await redis_manager.redis_client.hincrby(cache_key, "total_tokens", total_tokens)
        
        # No expiration (unlimited TTL)
        
    except Exception as e:
        logger.error(f"Error updating daily usage cache: {e}")


async def queue_activity_log(
    username: str,
    model_name: str,
    request_type: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0
):
    """
    Add activity log to Redis queue for batch processing
    
    Args:
        username: Username
        model_name: Model name
        request_type: Request type (chat, generate, embeddings, etc.)
        prompt_tokens: Number of prompt tokens
        completion_tokens: Number of completion tokens
        total_tokens: Total tokens used
    """
    # Import at function level to get the initialized instance
    from app.redis import redis_manager
    
    QUEUE_KEY = "activity_log_queue"
    BATCH_SIZE = 50
    
    try:
        # Debug: Check redis_manager state
        logger.info(f"Redis manager state: has_redis_client={hasattr(redis_manager, 'redis_client')}, "
                   f"_connected={getattr(redis_manager, '_connected', False)}, "
                   f"redis_client_is_none={redis_manager.redis_client is None if hasattr(redis_manager, 'redis_client') else 'N/A'}")
        
        # Ensure Redis connection is available
        if not hasattr(redis_manager, 'redis_client') or redis_manager.redis_client is None:
            logger.warning(f"Redis client not initialized, skipping activity log queuing")
            return
        
        # Try to ping Redis to ensure connection
        try:
            await redis_manager.redis_client.ping()
            logger.info("Redis ping successful")
        except Exception as ping_error:
            logger.warning(f"Redis connection check failed: {ping_error}")
            return
        
        # Create log entry
        log_data = {
            "username": username,
            "model_name": model_name,
            "request_type": request_type,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens or (prompt_tokens + completion_tokens),
            "timestamp": datetime.utcnow().isoformat()
        }
        
        # Add to queue
        await redis_manager.redis_client.rpush(QUEUE_KEY, json.dumps(log_data))
        
        # Check queue size
        queue_size = await redis_manager.redis_client.llen(QUEUE_KEY)
        
        # If queue is full, trigger immediate flush
        if queue_size >= BATCH_SIZE:
            flush_activity_logs_task.delay()
    
    except Exception as e:
        logger.error(f"Error queuing activity log: {e}")


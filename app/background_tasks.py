"""Background task manager for async processing"""

import asyncio
import json
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from app.redis import redis_manager

logger = logging.getLogger(__name__)

# Configuration
QUEUE_KEY = "activity_log_queue"
BATCH_SIZE = 50
POLL_INTERVAL = 2.0  # seconds
SHUTDOWN_EVENT = asyncio.Event()


async def queue_activity_log_async(
    username: str,
    model_name: str,
    request_type: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0
):
    """
    Add activity log to Redis queue for background processing
    
    Args:
        username: Username
        model_name: Model name
        request_type: Request type (chat, generate, embeddings, etc.)
        prompt_tokens: Number of prompt tokens
        completion_tokens: Number of completion tokens
        total_tokens: Total tokens used
    """
    try:
        if not redis_manager._connected or not redis_manager.redis_client:
            logger.warning("Redis not connected, skipping activity log")
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
        
        # Add to queue (RPUSH for FIFO)
        # Redis client has decode_responses=True, so we pass strings directly
        await redis_manager.redis_client.rpush(QUEUE_KEY, json.dumps(log_data))
        
        # Log successful queue
        logger.debug(f"Queued activity log for user {username}")
        
    except Exception as e:
        logger.error(f"Error queuing activity log: {e}")


async def _update_daily_usage_cache(username: str, total_tokens: int):
    """
    Update daily usage cache with atomic increment
    
    Args:
        username: Username
        total_tokens: Number of tokens to add
    """
    try:
        if not redis_manager._connected or not redis_manager.redis_client:
            return
        
        today = datetime.utcnow().strftime("%Y-%m-%d")
        cache_key = f"user_daily_usage:{username}:{today}"
        
        # Delete existing key if it exists and has wrong type
        key_exists = await redis_manager.redis_client.exists(cache_key)
        if key_exists:
            key_type = await redis_manager.redis_client.type(cache_key)
            if key_type != 'hash':
                # Wrong type, delete it
                logger.warning(f"Cache key {cache_key} has wrong type ({key_type}), deleting...")
                await redis_manager.redis_client.delete(cache_key)
        
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
        
    except Exception as e:
        logger.error(f"Error updating daily usage cache: {e}")


async def process_batch(batch: List[Dict[str, Any]]):
    """
    Process a batch of activity logs
    
    Args:
        batch: List of activity log dictionaries
    """
    if not batch:
        return
    
    try:
        from app.repositories import UserRepository, UserActivityRepository
        from app.database import async_session_maker
        
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            activity_repo = UserActivityRepository(session)
            
            for log in batch:
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
                    logger.error(f"Error processing activity log: {e}")
                    continue
            
            # Commit all logs in batch
            await session.commit()
            logger.info(f"Processed {len(batch)} activity logs")
    
    except Exception as e:
        logger.error(f"Error processing batch: {e}")


async def background_processor():
    """
    Background processor that periodically processes activity logs from Redis queue
    """
    logger.info("Starting background activity log processor")
    
    while not SHUTDOWN_EVENT.is_set():
        try:
            if not redis_manager._connected or not redis_manager.redis_client:
                await asyncio.sleep(POLL_INTERVAL)
                continue
            
            # Check queue size
            queue_size = await redis_manager.redis_client.llen(QUEUE_KEY)
            
            if queue_size == 0:
                # No logs to process, wait a bit
                await asyncio.sleep(POLL_INTERVAL)
                continue
            
            # Get batch of logs
            batch_size = min(queue_size, BATCH_SIZE)
            batch = []
            
            for _ in range(batch_size):
                if SHUTDOWN_EVENT.is_set():
                    break
                
                try:
                    # Get log from queue (LPOP for FIFO)
                    log_data = await redis_manager.redis_client.lpop(QUEUE_KEY)
                    if log_data:
                        # log_data is already a string (decode_responses=True)
                        batch.append(json.loads(log_data))
                except (json.JSONDecodeError, TypeError) as e:
                    logger.error(f"Error decoding log data: {e}")
                    continue
            
            # Process batch
            if batch:
                await process_batch(batch)
            
        except Exception as e:
            logger.error(f"Error in background processor: {e}")
            await asyncio.sleep(POLL_INTERVAL)
    
    logger.info("Background activity log processor stopped")


async def start_background_tasks():
    """Start background task processor"""
    # Start the background processor as a task
    asyncio.create_task(background_processor())
    logger.info("Background tasks started")


async def stop_background_tasks():
    """Stop background task processor gracefully"""
    logger.info("Stopping background tasks...")
    SHUTDOWN_EVENT.set()
    
    # Process remaining logs in queue
    try:
        if redis_manager._connected and redis_manager.redis_client:
            queue_size = await redis_manager.redis_client.llen(QUEUE_KEY)
            if queue_size > 0:
                logger.info(f"Processing {queue_size} remaining logs...")
                batch = []
                
                for _ in range(queue_size):
                    try:
                        log_data = await redis_manager.redis_client.lpop(QUEUE_KEY)
                        if log_data:
                            batch.append(json.loads(log_data))
                    except json.JSONDecodeError:
                        continue
                
                if batch:
                    await process_batch(batch)
    except Exception as e:
        logger.error(f"Error processing remaining logs: {e}")
    
    logger.info("Background tasks stopped") 

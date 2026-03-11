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


# =============================================================================
# NODE HEALTH CHECK & MODEL DISCOVERY TASKS
# =============================================================================

# Configuration for node tasks
NODE_HEALTH_CHECK_INTERVAL = 60.0  # seconds
MODEL_SYNC_INTERVAL = 300.0  # seconds (5 minutes)
NODE_TASK_SHUTDOWN_EVENT = asyncio.Event()


async def node_health_check_task():
    """
    Periodically check health of all active nodes.
    Updates node health status in database.
    """
    logger.info("Starting node health check task")
    
    while not NODE_TASK_SHUTDOWN_EVENT.is_set():
        try:
            from app.database import async_session_maker
            from app.node_manager import node_manager
            from app.repositories.node_repository import NodeRepository
            
            async with async_session_maker() as session:
                node_repo = NodeRepository(session)
                nodes = await node_repo.list_active()
                
                for node in nodes:
                    is_healthy, error = await node_manager.health_check_node(
                        node.base_url,
                        node.api_key,
                        timeout=5.0
                    )
                    
                    status = "healthy" if is_healthy else "unhealthy"
                    await node_repo.update_health_status(
                        node.id,
                        status,
                        error if not is_healthy else None
                    )
                    
                    logger.debug(f"Node '{node.name}' health: {status}")
                
                await session.commit()
                
        except Exception as e:
            logger.error(f"Error in health check task: {e}")
        
        # Wait for next interval or shutdown
        try:
            await asyncio.wait_for(
                NODE_TASK_SHUTDOWN_EVENT.wait(),
                timeout=NODE_HEALTH_CHECK_INTERVAL
            )
            break  # Shutdown requested
        except asyncio.TimeoutError:
            pass  # Continue to next iteration
    
    logger.info("Node health check task stopped")


async def model_discovery_task():
    """
    Periodically discover models from all healthy nodes.
    Updates node_models table.
    """
    logger.info("Starting model discovery task")
    
    while not NODE_TASK_SHUTDOWN_EVENT.is_set():
        try:
            from app.database import async_session_maker
            from app.node_manager import node_manager
            
            async with async_session_maker() as session:
                result = await node_manager.sync_all_nodes(session)
                
                logger.info(
                    f"Model discovery: {result['successful_nodes']}/{result['total_nodes']} nodes, "
                    f"{result['total_models']} models"
                )
                
                if result['failed_nodes']:
                    logger.warning(f"Failed nodes: {result['failed_nodes']}")
                    
        except Exception as e:
            logger.error(f"Error in model discovery task: {e}")
        
        # Wait for next interval or shutdown
        try:
            await asyncio.wait_for(
                NODE_TASK_SHUTDOWN_EVENT.wait(),
                timeout=MODEL_SYNC_INTERVAL
            )
            break  # Shutdown requested
        except asyncio.TimeoutError:
            pass  # Continue to next iteration
    
    logger.info("Model discovery task stopped")


async def node_load_cleanup_task():
    """
    Clean up old load metrics and reset counters.
    Run every hour.
    """
    logger.info("Starting node load cleanup task")
    
    while not NODE_TASK_SHUTDOWN_EVENT.is_set():
        try:
            from app.database import async_session_maker
            from app.repositories.node_repository import NodeLoadMetricRepository
            
            async with async_session_maker() as session:
                metric_repo = NodeLoadMetricRepository(session)
                
                # Reset daily counters (at midnight)
                from datetime import datetime
                if datetime.utcnow().hour == 0:
                    await metric_repo.reset_daily_counters()
                    logger.info("Reset daily request counters")
                
                # Reset 5-minute counters every 5 minutes
                await metric_repo.reset_5min_counters()
                
        except Exception as e:
            logger.error(f"Error in load cleanup task: {e}")
        
        # Wait 1 hour
        try:
            await asyncio.wait_for(
                NODE_TASK_SHUTDOWN_EVENT.wait(),
                timeout=3600.0
            )
            break
        except asyncio.TimeoutError:
            pass
    
    logger.info("Node load cleanup task stopped")


# =============================================================================
# MODEL KEEP-WARM TASK
# =============================================================================
# Ollama unloads models from VRAM after keep_alive timeout (default: 5 min).
# This task proactively pings configured models every WARMUP_INTERVAL seconds
# to prevent cold-start timeouts on scheduled/cron requests.
# =============================================================================

WARMUP_INTERVAL = 240.0  # seconds — ping every 4 min (before 5-min unload)
WARMUP_SHUTDOWN_EVENT = asyncio.Event()

# Models to keep warm. If empty, the task reads from model_mappings at startup.
WARMUP_MODELS: list[str] = []


async def _get_running_models() -> set[str]:
    """Return set of real model names currently loaded in Ollama via /api/ps."""
    try:
        import httpx
        from app.config import get_settings
        settings = get_settings()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/ps")
            if resp.status_code == 200:
                data = resp.json()
                return {m.get("model", "") for m in data.get("models", [])}
    except Exception as e:
        logger.warning(f"[WARMUP] Could not fetch /api/ps: {e}")
    return set()


async def _warmup_model(real_name: str):
    """
    Send a minimal /api/generate request to load the model into VRAM.
    keep_alive=-1 keeps it loaded until the server restarts.
    """
    try:
        import httpx
        from app.config import get_settings
        settings = get_settings()
        payload = {
            "model": real_name,
            "prompt": "",          # empty prompt — just loads the model
            "stream": False,
            "keep_alive": -1,      # keep in VRAM forever
        }
        async with httpx.AsyncClient(timeout=600.0) as client:  # 10-min timeout for large models
            resp = await client.post(
                f"{settings.ollama_base_url}/api/generate",
                json=payload,
            )
            if resp.status_code == 200:
                logger.info(f"[WARMUP] ✅ Model '{real_name}' loaded and kept warm")
            else:
                logger.warning(f"[WARMUP] ⚠️ Unexpected status {resp.status_code} for '{real_name}'")
    except Exception as e:
        logger.warning(f"[WARMUP] ⚠️ Failed to warm '{real_name}': {e}")


async def model_keep_warm_task():
    """
    Background task: keep configured models loaded in Ollama.

    On each iteration:
    1. Get the list of models to keep warm (from WARMUP_MODELS or DB mappings).
    2. Check which ones are currently running via /api/ps.
    3. For any model NOT running, send an empty generate request to preload it.
    """
    logger.info("[WARMUP] Model keep-warm task started")

    while not WARMUP_SHUTDOWN_EVENT.is_set():
        try:
            from app.config import model_mapper

            # Determine target models
            targets: list[str] = list(WARMUP_MODELS)
            if not targets:
                # Fall back to all configured real model names from mappings
                await model_mapper.ensure_loaded()
                seen: set[str] = set()
                for real_name in model_mapper._mappings.values():
                    if real_name and real_name not in seen:
                        seen.add(real_name)
                        targets.append(real_name)

            if not targets:
                logger.debug("[WARMUP] No models configured, skipping")
            else:
                running = await _get_running_models()
                cold_models = [m for m in targets if m not in running]

                if cold_models:
                    logger.info(f"[WARMUP] Cold models detected: {cold_models}")
                    for model_name in cold_models:
                        if WARMUP_SHUTDOWN_EVENT.is_set():
                            break
                        await _warmup_model(model_name)
                else:
                    logger.debug(f"[WARMUP] All {len(targets)} model(s) already warm")

        except Exception as e:
            logger.error(f"[WARMUP] Error in keep-warm task: {e}")

        # Wait for next interval or shutdown
        try:
            await asyncio.wait_for(
                WARMUP_SHUTDOWN_EVENT.wait(),
                timeout=WARMUP_INTERVAL,
            )
            break  # Shutdown requested
        except asyncio.TimeoutError:
            pass  # Continue

    logger.info("[WARMUP] Model keep-warm task stopped")


async def start_background_tasks():
    """Start all background task processors"""
    # Start activity log processor
    asyncio.create_task(background_processor())
    
    # Start node health check task
    asyncio.create_task(node_health_check_task())
    
    # Start model discovery task
    asyncio.create_task(model_discovery_task())
    
    # Start load cleanup task
    asyncio.create_task(node_load_cleanup_task())

    # Start model keep-warm task (prevents cold-start timeouts)
    asyncio.create_task(model_keep_warm_task())
    
    logger.info("All background tasks started")


async def stop_background_tasks():
    """Stop all background task processors gracefully"""
    logger.info("Stopping background tasks...")
    
    # Signal shutdown
    SHUTDOWN_EVENT.set()
    NODE_TASK_SHUTDOWN_EVENT.set()
    WARMUP_SHUTDOWN_EVENT.set()
    
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
    
    # Close node manager HTTP client
    try:
        from app.node_manager import node_manager
        await node_manager.close()
    except Exception as e:
        logger.error(f"Error closing node manager: {e}")
    
    logger.info("Background tasks stopped") 

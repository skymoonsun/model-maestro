"""Ollama proxy logic and model name manipulation"""

from typing import Dict, Any, Optional
import httpx
import json
import logging
from fastapi import HTTPException, Depends
from fastapi.responses import StreamingResponse

from app.config import get_settings, model_mapper
from app.user_manager import user_manager
from app.auth import get_current_user

logger = logging.getLogger(__name__)


class OllamaProxy:
    """Proxy requests to Ollama with model name manipulation"""
    
    def __init__(self):
        self.settings = get_settings()
        self.base_url = self.settings.ollama_base_url
        self._mappings_loaded = False
        self._http_client: Optional[httpx.AsyncClient] = None
    
    async def _ensure_mappings_loaded(self):
        """Ensure model mappings are loaded from database"""
        # Only load once at startup, rely on cache invalidation
        if not self._mappings_loaded:
            await model_mapper.ensure_loaded()
            self._mappings_loaded = True
    
    async def _get_http_client(self) -> httpx.AsyncClient:
        """
        Get or create persistent HTTP client with connection pooling
        
        Returns:
            Configured AsyncClient with HTTP/2 support
        """
        if self._http_client is None:
            # Configure connection limits
            limits = httpx.Limits(
                max_keepalive_connections=20,
                max_connections=50,
                keepalive_expiry=300  # 5 minutes
            )
            
            self._http_client = httpx.AsyncClient(
                timeout=600.0,
                limits=limits,
                http2=True  # Enable HTTP/2
            )
        
        return self._http_client
    
    async def close(self):
        """Close HTTP client connection pool"""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
    
    def _map_model_to_ollama(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Map model names in request data from client format to Ollama format
        
        Args:
            data: Request data with potential model field
        
        Returns:
            Modified data with real model names
        """
        if not data:
            return data
        
        data_copy = data.copy()
        
        # Handle 'model' field
        if 'model' in data_copy:
            data_copy['model'] = model_mapper.get_real_model_name(data_copy['model'])
        
        # Handle 'name' field (used in show, delete, pull, push)
        if 'name' in data_copy:
            data_copy['name'] = model_mapper.get_real_model_name(data_copy['name'])
        
        # Handle 'source' and 'destination' fields (used in copy)
        if 'source' in data_copy:
            data_copy['source'] = model_mapper.get_real_model_name(data_copy['source'])
        if 'destination' in data_copy:
            data_copy['destination'] = model_mapper.get_real_model_name(data_copy['destination'])
        
        return data_copy
    
    async def _map_model_to_display(self, real_name: str) -> str:
        """
        Map real model name to display name (reverse mapping)
        
        Args:
            real_name: Real model name from Ollama
        
        Returns:
            Display model name for client
        """
        await self._ensure_mappings_loaded()
        return model_mapper.get_display_model_name(real_name)
    
    def _map_model_from_ollama(self, data: Any) -> Any:
        """
        Map model names in response data from Ollama format to client format
        
        Args:
            data: Response data with potential model fields
        
        Returns:
            Modified data with display model names
        """
        if isinstance(data, dict):
            data_copy = data.copy()
            
            # Handle 'model' field
            if 'model' in data_copy:
                data_copy['model'] = model_mapper.get_display_model_name(data_copy['model'])
            
            # Handle 'name' field
            if 'name' in data_copy:
                data_copy['name'] = model_mapper.get_display_model_name(data_copy['name'])
            
            # Handle 'parent_model' field
            if 'parent_model' in data_copy:
                data_copy['parent_model'] = model_mapper.get_display_model_name(data_copy['parent_model'])
            
            # Remove remote_model field to make cloud models look like local models
            if 'remote_model' in data_copy:
                del data_copy['remote_model']
            
            # Remove remote_host field to make cloud models look like local models
            if 'remote_host' in data_copy:
                del data_copy['remote_host']
            
            # Handle nested details
            if 'details' in data_copy and isinstance(data_copy['details'], dict):
                if 'parent_model' in data_copy['details']:
                    data_copy['details']['parent_model'] = model_mapper.get_display_model_name(
                        data_copy['details']['parent_model']
                    )
            
            return data_copy
        
        return data
    
    def _map_models_list(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Map model names in /api/tags response
        
        Args:
            data: Response from /api/tags
        
        Returns:
            Modified data with display model names
        """
        if not isinstance(data, dict) or 'models' not in data:
            return data
        
        data_copy = data.copy()
        models = []
        
        for model in data_copy.get('models', []):
            model_copy = model.copy() if isinstance(model, dict) else model
            
            if isinstance(model_copy, dict):
                # Map name field
                if 'name' in model_copy:
                    model_copy['name'] = model_mapper.get_display_model_name(model_copy['name'])
                
                # Map model field if exists
                if 'model' in model_copy:
                    model_copy['model'] = model_mapper.get_display_model_name(model_copy['model'])
                
                # Remove remote_model field to make cloud models look like local models
                if 'remote_model' in model_copy:
                    del model_copy['remote_model']
                
                # Remove remote_host field to make cloud models look like local models
                if 'remote_host' in model_copy:
                    del model_copy['remote_host']
                
                # Map parent_model in details
                if 'details' in model_copy and isinstance(model_copy['details'], dict):
                    if 'parent_model' in model_copy['details']:
                        model_copy['details']['parent_model'] = model_mapper.get_display_model_name(
                            model_copy['details']['parent_model']
                        )
            
            models.append(model_copy)
        
        data_copy['models'] = models
        return data_copy
    
    def _map_openai_models_list(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Map model names in /v1/models response (OpenAI compatible format)
        
        Args:
            data: Response from /v1/models
        
        Returns:
            Modified data with display model names
        """
        if not isinstance(data, dict) or 'data' not in data:
            return data
        
        data_copy = data.copy()
        models = []
        
        for model in data_copy.get('data', []):
            model_copy = model.copy() if isinstance(model, dict) else model
            
            if isinstance(model_copy, dict):
                # Map id field (model name in OpenAI format)
                if 'id' in model_copy:
                    model_copy['id'] = model_mapper.get_display_model_name(model_copy['id'])
            
            models.append(model_copy)
        
        data_copy['data'] = models
        return data_copy
    
    async def check_user_limits(self, username: str, request_type: str) -> bool:
        """
        Check if user has exceeded their limits (with Redis caching)
        
        Args:
            username: Username
            request_type: Type of request (generate, chat, embeddings, etc.)
        
        Returns:
            True if user is within limits, False otherwise
        """
        from datetime import datetime, timedelta
        from app.redis import redis_manager, CACHE_KEYS, CACHE_TTL
        
        # 1. Get user limit from cache or DB
        limit_cache_key = CACHE_KEYS["USER_LIMIT"].format(username=username)
        user_limit = await redis_manager.get(limit_cache_key)
        
        if not user_limit:
            # Cache miss - get from DB
            user_limit = await user_manager.get_user_limit(username)
            if user_limit:
                await redis_manager.set(limit_cache_key, user_limit, expire=CACHE_TTL["USER_LIMIT"])
            else:
                # No limits set, allow request
                return True
        
        # 2. Get daily usage from cache or DB
        today = datetime.utcnow().strftime("%Y-%m-%d")
        usage_cache_key = CACHE_KEYS["USER_DAILY_USAGE"].format(username=username, date=today)
        
        # Try to get from cache (as hash)
        daily_usage = None
        try:
            if redis_manager._connected and redis_manager.redis_client:
                # Check if key exists and get type
                key_exists = await redis_manager.redis_client.exists(usage_cache_key)
                if key_exists:
                    key_type = await redis_manager.redis_client.type(usage_cache_key)
                    if key_type == 'hash':
                        # Read as hash
                        hash_data = await redis_manager.redis_client.hgetall(usage_cache_key)
                        daily_usage = {
                            "total_requests": int(hash_data.get("total_requests", 0)),
                            "total_tokens": int(hash_data.get("total_tokens", 0)),
                            "prompt_tokens": int(hash_data.get("prompt_tokens", 0)),
                            "completion_tokens": int(hash_data.get("completion_tokens", 0))
                        }
                    else:
                        # Wrong type, delete it
                        await redis_manager.redis_client.delete(usage_cache_key)
        except Exception as e:
            logger.warning(f"Error reading daily usage cache: {e}")
        
        if not daily_usage:
            # Cache miss - get from DB
            start_of_day = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_day = start_of_day + timedelta(days=1)
            daily_usage = await user_manager.get_user_token_usage(username, start_of_day, end_of_day)
            if daily_usage and redis_manager._connected and redis_manager.redis_client:
                # Store as hash
                await redis_manager.redis_client.hset(
                    usage_cache_key,
                    mapping={
                        "total_requests": daily_usage.get("total_requests", 0),
                        "total_tokens": daily_usage.get("total_tokens", 0),
                        "prompt_tokens": daily_usage.get("prompt_tokens", 0),
                        "completion_tokens": daily_usage.get("completion_tokens", 0)
                    }
                )
        
        # 3. Check request limit
        request_limit = user_limit.get("request_limit")
        if request_limit is not None and daily_usage:
            if daily_usage.get("total_requests", 0) >= request_limit:
                return False
        
        # 4. Check token limit
        token_limit = user_limit.get("token_limit")
        if token_limit is not None and daily_usage:
            if daily_usage.get("total_tokens", 0) >= token_limit:
                return False
        
        return True
    
    async def _log_user_activity(
        self,
        username: str,
        model_name: str,
        request_type: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0
    ):
        """
        Log user activity for token usage and model access (batch processing)
        
        Queues the activity log for background batch processing.
        
        Args:
            username: Username
            model_name: Model name used
            request_type: Type of request (generate, chat, embeddings, etc.)
            prompt_tokens: Number of prompt tokens used
            completion_tokens: Number of completion tokens used
            total_tokens: Total tokens used
        """
        from app.background_tasks import queue_activity_log_async
        
        await queue_activity_log_async(
            username=username,
            model_name=model_name,
            request_type=request_type,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens or (prompt_tokens + completion_tokens)
        )
    
    async def proxy_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        stream: bool = False,
        username: Optional[str] = None
    ):
        """
        Proxy request to Ollama
        
        Args:
            method: HTTP method (GET, POST, DELETE)
            endpoint: Ollama API endpoint
            data: Request body data
            stream: Whether to stream the response
            username: Username for logging and limit checking
        
        Returns:
            Response from Ollama (mapped model names)
        """
        # Ensure model mappings are loaded from database
        await self._ensure_mappings_loaded()
        
        # Extract model name for logging
        model_name = None
        if data and isinstance(data, dict):
            model_name = data.get('model') or data.get('name')
        
        url = f"{self.base_url}{endpoint}"
        
        # Map model names in request
        if data:
            data = self._map_model_to_ollama(data)
        
        # Validate data for POST requests
        if method.upper() == "POST" and not data:
            raise HTTPException(
                status_code=400,
                detail="Request body is required for POST requests"
            )
        
        # Detect if this is an OpenAI-compatible endpoint (for SSE formatting)
        is_openai_endpoint = endpoint.startswith("/v1/")
        
        try:
            if method.upper() == "POST" and stream:
                # Handle streaming response with persistent HTTP client
                async def stream_generator():
                    client = await self._get_http_client()
                    
                    # Always log streaming requests for debugging
                    logger.info(f"[STREAM START] Sending streaming request to Ollama: {url}")
                    logger.info(f"[STREAM START] Model: {data.get('model', 'unknown')}, OpenAI endpoint: {is_openai_endpoint}")
                    
                    try:
                        async with client.stream("POST", url, json=data) as resp:
                            logger.info(f"[STREAM] Ollama response status: {resp.status_code}")
                            logger.info(f"[STREAM] Ollama response headers: {dict(resp.headers)}")
                            
                            # Check status code before streaming
                            if resp.status_code != 200:
                                error_text = await resp.aread()
                                error_msg = error_text.decode()
                                
                                # Try to parse error message if it's JSON
                                try:
                                    error_json = json.loads(error_msg)
                                    if isinstance(error_json, dict) and 'error' in error_json:
                                        error_detail = error_json['error']
                                        if isinstance(error_detail, dict) and 'message' in error_detail:
                                            error_msg = error_detail['message']
                                except (json.JSONDecodeError, KeyError, TypeError):
                                    # If not JSON or doesn't have expected structure, use as-is
                                    pass
                                
                                # Log the request data that caused the error for debugging
                                logger.error(f"Ollama upstream error ({resp.status_code}): {error_msg}")
                                logger.error(f"Request URL: {url}")
                                logger.error(f"Request data: {json.dumps(data, ensure_ascii=False, indent=2)}")
                                logger.error(f"Full error response: {error_text.decode()}")
                                
                                # Send error in SSE format for OpenAI compatibility
                                error_response = {
                                    "error": {
                                        "message": f"Ollama upstream error: {error_msg}",
                                        "type": "api_error",
                                        "code": resp.status_code
                                    }
                                }
                                yield b'data: ' + json.dumps(error_response, ensure_ascii=False).encode('utf-8') + b'\n\n'
                                yield b'data: [DONE]\n\n'
                                return
                            
                            # Buffer to accumulate partial lines
                            buffer = b""
                            prompt_tokens = 0
                            completion_tokens = 0
                            first_chunk_sent = False
                            done_marker_sent = False  # Track if [DONE] was already sent
                            chunk_count = 0
                            total_bytes = 0
                            
                            async for chunk in resp.aiter_raw():
                                if not chunk:
                                    continue
                                
                                chunk_count += 1
                                total_bytes += len(chunk)
                                
                                # Log first few chunks for debugging
                                if chunk_count <= 3:
                                    logger.info(f"[STREAM CHUNK {chunk_count}] Received {len(chunk)} bytes: {chunk[:200]!r}")
                                
                                # Add to buffer
                                buffer += chunk
                                
                                # Process complete lines
                                while b'\n' in buffer:
                                    line, buffer = buffer.split(b'\n', 1)
                                    if line:
                                        try:
                                            # Check if it's SSE format (for OpenAI endpoints)
                                            if line.startswith(b'data: '):
                                                # Extract JSON after "data: "
                                                json_str = line[6:].decode('utf-8').strip()
                                                if json_str and json_str != '[DONE]':
                                                    json_data = json.loads(json_str)
                                                    mapped_data = self._map_model_from_ollama(json_data)
                                                    
                                                    # Extract token usage if available
                                                    if isinstance(mapped_data, dict) and 'usage' in mapped_data:
                                                        usage = mapped_data['usage']
                                                        prompt_tokens += usage.get('prompt_tokens', 0)
                                                        completion_tokens += usage.get('completion_tokens', 0)
                                                    
                                                    # SSE format: data: {...}\n\n (double newline!)
                                                    yield b'data: ' + json.dumps(mapped_data, ensure_ascii=False).encode('utf-8') + b'\n\n'
                                                    first_chunk_sent = True
                                                elif json_str == '[DONE]':
                                                    # [DONE] marker - only send once!
                                                    if not done_marker_sent:
                                                        logger.info(f"[STREAM] Received [DONE] marker from Ollama, forwarding")
                                                        yield b'data: [DONE]\n\n'
                                                        done_marker_sent = True
                                                        first_chunk_sent = True
                                            else:
                                                # Regular Ollama format (NDJSON)
                                                json_data = json.loads(line.decode('utf-8'))
                                                mapped_data = self._map_model_from_ollama(json_data)
                                                
                                                # Extract token usage if available
                                                if isinstance(mapped_data, dict) and 'prompt_eval_count' in mapped_data:
                                                    prompt_tokens += mapped_data.get('prompt_eval_count', 0)
                                                if isinstance(mapped_data, dict) and 'eval_count' in mapped_data:
                                                    completion_tokens += mapped_data.get('eval_count', 0)
                                                
                                                # For OpenAI endpoints, convert NDJSON to SSE format
                                                if is_openai_endpoint:
                                                    yield b'data: ' + json.dumps(mapped_data, ensure_ascii=False).encode('utf-8') + b'\n\n'
                                                else:
                                                    yield json.dumps(mapped_data, ensure_ascii=False).encode('utf-8') + b'\n'
                                                first_chunk_sent = True
                                        except (json.JSONDecodeError, UnicodeDecodeError) as e:
                                            # Log parse errors for debugging
                                            logger.warning(f"[STREAM] JSON parse error: {e}, line: {line[:100]!r}")
                                            # If not valid JSON, pass through as-is with proper format
                                            if is_openai_endpoint:
                                                yield b'data: ' + line + b'\n\n'
                                            else:
                                                yield line + b'\n'
                                            first_chunk_sent = True
                            
                            # Process any remaining data in buffer
                            if buffer:
                                logger.info(f"[STREAM] Processing remaining buffer: {len(buffer)} bytes")
                                try:
                                    # Try to strip any trailing whitespace for clean JSON parsing
                                    buffer_stripped = buffer.strip()
                                    if buffer_stripped:
                                        json_data = json.loads(buffer_stripped.decode('utf-8'))
                                        mapped_data = self._map_model_from_ollama(json_data)
                                        
                                        # Extract token usage if available
                                        if isinstance(mapped_data, dict) and 'prompt_eval_count' in mapped_data:
                                            prompt_tokens += mapped_data.get('prompt_eval_count', 0)
                                        if isinstance(mapped_data, dict) and 'eval_count' in mapped_data:
                                            completion_tokens += mapped_data.get('eval_count', 0)
                                        
                                        if is_openai_endpoint:
                                            yield b'data: ' + json.dumps(mapped_data, ensure_ascii=False).encode('utf-8') + b'\n\n'
                                        else:
                                            yield json.dumps(mapped_data, ensure_ascii=False).encode('utf-8') + b'\n'
                                        first_chunk_sent = True
                                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                                    logger.warning(f"[STREAM] Buffer parse error: {e}, buffer: {buffer[:100]!r}")
                                    if buffer.strip():
                                        if is_openai_endpoint:
                                            yield b'data: ' + buffer + b'\n\n'
                                        else:
                                            yield buffer
                                        first_chunk_sent = True
                            
                            # For OpenAI endpoints, send [DONE] marker if not already sent
                            if is_openai_endpoint and not done_marker_sent:
                                logger.info(f"[STREAM END] Sending [DONE] marker (not received from upstream). Total chunks: {chunk_count}, bytes: {total_bytes}")
                                yield b'data: [DONE]\n\n'
                            elif is_openai_endpoint:
                                logger.info(f"[STREAM END] Stream complete ([DONE] already sent). Total chunks: {chunk_count}, bytes: {total_bytes}")
                            else:
                                logger.info(f"[STREAM END] Non-OpenAI stream complete. Total chunks: {chunk_count}, bytes: {total_bytes}")
                            
                            # Log user activity after streaming is complete
                            if username and model_name:
                                await self._log_user_activity(
                                    username=username,
                                    model_name=model_name,
                                    request_type=endpoint.replace('/api/', '').replace('/v1/', ''),
                                    prompt_tokens=prompt_tokens,
                                    completion_tokens=completion_tokens,
                                    total_tokens=prompt_tokens + completion_tokens
                                )
                    except httpx.RequestError as e:
                        # Network/connection errors
                        logger.error(f"Network error while streaming to Ollama: {str(e)}")
                        logger.error(f"Request URL: {url}")
                        error_response = {
                            "error": {
                                "message": f"Failed to connect to Ollama: {str(e)}",
                                "type": "connection_error",
                                "code": 503
                            }
                        }
                        yield b'data: ' + json.dumps(error_response, ensure_ascii=False).encode('utf-8') + b'\n\n'
                        yield b'data: [DONE]\n\n'
                    except Exception as e:
                        # Any other unexpected errors
                        logger.error(f"Unexpected error while streaming: {str(e)}", exc_info=True)
                        logger.error(f"Request URL: {url}")
                        error_response = {
                            "error": {
                                "message": f"Unexpected error: {str(e)}",
                                "type": "internal_error",
                                "code": 500
                            }
                        }
                        yield b'data: ' + json.dumps(error_response, ensure_ascii=False).encode('utf-8') + b'\n\n'
                        yield b'data: [DONE]\n\n'
                
                # Return streaming response with proper headers for SSE
                # OpenAI endpoints use text/event-stream, native Ollama uses application/x-ndjson
                if is_openai_endpoint:
                    media_type = "text/event-stream"
                else:
                    media_type = "application/x-ndjson"
                
                response = StreamingResponse(
                    stream_generator(),
                    media_type=media_type,
                    headers={
                        "Cache-Control": "no-cache, no-store, must-revalidate",
                        "X-Accel-Buffering": "no",
                        "Connection": "keep-alive",
                        "Access-Control-Allow-Origin": "*",
                    }
                )
                return response
            
            # Non-streaming requests with persistent HTTP client
            client = await self._get_http_client()
            if method.upper() == "GET":
                response = await client.get(url)
            elif method.upper() == "POST":
                # Log request data for debugging (only in development)
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"Sending request to Ollama: {url}, data: {json.dumps(data, ensure_ascii=False)}")
                response = await client.post(url, json=data)
            elif method.upper() == "DELETE":
                response = await client.delete(url, json=data)
            else:
                raise HTTPException(status_code=405, detail="Method not allowed")
            
            # Check response status
            if response.status_code >= 400:
                # Log the request data that caused the error for debugging
                logger.error(f"Ollama error ({response.status_code}): {response.text}")
                logger.error(f"Request URL: {url}")
                if data:
                    logger.error(f"Request data: {json.dumps(data, ensure_ascii=False, indent=2)}")
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Ollama error: {response.text}"
                )
            
            # Parse response
            try:
                response_data = response.json()
            except:
                # Log user activity for non-JSON responses
                if username and model_name:
                    await self._log_user_activity(
                        username=username,
                        model_name=model_name,
                        request_type=endpoint.replace('/api/', '').replace('/v1/', ''),
                        prompt_tokens=0,
                        completion_tokens=0,
                        total_tokens=0
                    )
                return response.text
            
            # Map model names in response
            if endpoint == "/api/tags":
                response_data = self._map_models_list(response_data)
            elif endpoint == "/v1/models":
                response_data = self._map_openai_models_list(response_data)
            else:
                response_data = self._map_model_from_ollama(response_data)
            
            # Extract token usage for logging
            prompt_tokens = 0
            completion_tokens = 0
            total_tokens = 0
            
            if isinstance(response_data, dict):
                # For chat/generate responses
                if 'prompt_eval_count' in response_data:
                    prompt_tokens = response_data.get('prompt_eval_count', 0)
                if 'eval_count' in response_data:
                    completion_tokens = response_data.get('eval_count', 0)
                if 'total_duration' in response_data and 'load_duration' in response_data:
                    # Estimate tokens for embeddings (approximate)
                    total_tokens = prompt_tokens + completion_tokens
                
                # For OpenAI format responses
                if 'usage' in response_data:
                    usage = response_data['usage']
                    prompt_tokens = usage.get('prompt_tokens', 0)
                    completion_tokens = usage.get('completion_tokens', 0)
                    total_tokens = usage.get('total_tokens', 0)
            
            # Log user activity
            if username and model_name:
                await self._log_user_activity(
                    username=username,
                    model_name=model_name,
                    request_type=endpoint.replace('/api/', '').replace('/v1/', ''),
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens or (prompt_tokens + completion_tokens)
                )
            
            return response_data
                
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=503,
                detail=f"Failed to connect to Ollama: {str(e)}"
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Proxy error: {str(e)}"
            )


# Global proxy instance
ollama_proxy = OllamaProxy()


"""Ollama proxy logic and model name manipulation"""

from typing import Dict, Any, Optional, List, Tuple
import httpx
import json
import logging
import re
import uuid
from fastapi import HTTPException, Depends
from fastapi.responses import StreamingResponse

from app.config import get_settings, model_mapper
from app.user_manager import user_manager
from app.auth import get_current_user

logger = logging.getLogger(__name__)


# ============================================================================
# TOOL CALL VALIDATION (LiteLLM-inspired)
# ============================================================================
def _is_tool_call_valid(tool_call: Dict[str, Any]) -> bool:
    """
    Check if a tool call has valid and complete arguments.

    This is critical for Cursor compatibility - incomplete or invalid tool calls
    should not be buffered/yielded as they cause Cursor to hang.

    Args:
        tool_call: Tool call object with function.name and function.arguments

    Returns:
        True if tool call is complete and valid, False otherwise
    """
    if not isinstance(tool_call, dict):
        return False

    func = tool_call.get('function', {})
    if not isinstance(func, dict):
        return False

    args = func.get('arguments', '')

    # Empty arguments is valid (no-arg function)
    if not args:
        return True

    # Check if arguments is valid JSON
    try:
        json.loads(args)
        return True
    except (json.JSONDecodeError, TypeError):
        return False


# ============================================================================
# KIMI TOOL CALL CONVERTER
# ============================================================================
# Kimi models use a custom tool call format that needs to be converted
# to OpenAI's standard tool_calls format for Cursor IDE compatibility.
#
# Kimi format:
#   <|tool_calls_section_begin|>
#   <|tool_call_begin|>functions.FunctionName:index<|tool_call_argument_begin|>
#   {"arg1": "value1", ...}
#   <|tool_call_end|>
#   <|tool_calls_section_end|>
#
# OpenAI format (in delta):
#   {"tool_calls": [{"index": 0, "id": "call_xxx", "type": "function", 
#     "function": {"name": "FunctionName", "arguments": "{...}"}}]}
# ============================================================================

# Regex patterns for Kimi tool call format
KIMI_TOOL_CALL_SECTION_START = r'<\|tool_calls_section_begin\|>'
KIMI_TOOL_CALL_SECTION_END = r'<\|tool_calls_section_end\|>'

# More flexible pattern that handles nested JSON objects
# Uses lazy matching to find content between markers
KIMI_TOOL_CALL_PATTERN = re.compile(
    r'<\|tool_call_begin\|>\s*'
    r'(?:functions\.)?(\w+)(?::\d+)?\s*'
    r'<\|tool_call_argument_begin\|>\s*'
    r'(\{.*?\})\s*'  # Lazy match for JSON - handles nested objects
    r'<\|tool_call_end\|>',
    re.DOTALL
)


def parse_kimi_tool_calls(content: str) -> Tuple[str, List[Dict[str, Any]], bool]:
    """
    Parse Kimi tool call format from content and convert to OpenAI format.
    
    Args:
        content: The content string that may contain Kimi tool calls
        
    Returns:
        Tuple of:
        - clean_content: Content with tool call markers removed
        - tool_calls: List of OpenAI-formatted tool call objects
        - has_tool_calls: Whether any tool calls were found
    """
    if not content:
        return content, [], False
    
    # Check if content contains Kimi tool call markers
    section_start = '<|tool_calls_section_begin|>'
    section_end = '<|tool_calls_section_end|>'
    
    if section_start not in content:
        return content, [], False
    
    tool_calls = []
    tool_call_index = 0
    
    # Find the section boundaries
    start_idx = content.find(section_start)
    end_idx = content.find(section_end)
    
    if start_idx == -1:
        return content, [], False
    
    # Extract content before and after the tool call section
    content_before = content[:start_idx].strip()
    content_after = content[end_idx + len(section_end):].strip() if end_idx != -1 else ""
    
    # Extract the tool call section
    if end_idx != -1:
        section_content = content[start_idx + len(section_start):end_idx]
    else:
        section_content = content[start_idx + len(section_start):]
    
    # Parse individual tool calls from the section
    tool_call_begin = '<|tool_call_begin|>'
    tool_call_end = '<|tool_call_end|>'
    arg_begin = '<|tool_call_argument_begin|>'
    
    current_pos = 0
    while True:
        # Find next tool call
        call_start = section_content.find(tool_call_begin, current_pos)
        if call_start == -1:
            break
        
        call_end = section_content.find(tool_call_end, call_start)
        if call_end == -1:
            break
        
        # Extract the tool call content
        call_content = section_content[call_start + len(tool_call_begin):call_end].strip()
        
        # Find the argument marker
        arg_start = call_content.find(arg_begin)
        if arg_start == -1:
            current_pos = call_end + len(tool_call_end)
            continue
        
        # Extract function name (everything before arg_begin)
        func_part = call_content[:arg_start].strip()
        # Remove "functions." prefix if present
        if func_part.startswith('functions.'):
            func_part = func_part[10:]
        # Remove trailing index like ":11"
        if ':' in func_part:
            func_part = func_part.split(':')[0]
        function_name = func_part.strip()
        
        # Extract arguments JSON (everything after arg_begin)
        args_str = call_content[arg_start + len(arg_begin):].strip()
        
        # Parse JSON arguments
        try:
            arguments = json.loads(args_str)
            arguments_str = json.dumps(arguments, ensure_ascii=False)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse Kimi tool call arguments: {args_str[:100]}... Error: {e}")
            current_pos = call_end + len(tool_call_end)
            continue
        
        tool_call = {
            "index": tool_call_index,
            "id": f"call_{uuid.uuid4().hex[:24]}",
            "type": "function",
            "function": {
                "name": function_name,
                "arguments": arguments_str
            }
        }
        tool_calls.append(tool_call)
        tool_call_index += 1
        logger.info(f"[KIMI] Parsed tool call: {function_name}({arguments_str[:50]}...)")
        
        current_pos = call_end + len(tool_call_end)
    
    # Combine clean content
    clean_content = f"{content_before} {content_after}".strip()
    
    return clean_content, tool_calls, len(tool_calls) > 0


def convert_kimi_content_to_openai_delta(content: str, model: str) -> List[Dict[str, Any]]:
    """
    Convert Kimi content with tool calls to OpenAI delta format chunks.
    
    Args:
        content: Content that may contain Kimi tool calls
        model: Model name for the response
        
    Returns:
        List of OpenAI-formatted delta chunks to send
    """
    clean_content, tool_calls, has_tool_calls = parse_kimi_tool_calls(content)
    
    chunks = []
    
    # If there's clean content before/after tool calls, send it as regular content
    if clean_content:
        chunks.append({
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {"content": clean_content},
                "finish_reason": None
            }]
        })
    
    # Send tool calls if present
    if has_tool_calls:
        # First chunk: tool call with function name and arguments
        chunks.append({
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {"tool_calls": tool_calls},
                "finish_reason": None
            }]
        })
        
        # Final chunk: finish_reason = tool_calls
        chunks.append({
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "tool_calls"
            }]
        })
    
    return chunks


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
            # Connection limits
            # Increased for agentic workflows (Cursor Agent can spawn multiple requests)
            limits = httpx.Limits(
                max_keepalive_connections=40,
                max_connections=100,
                keepalive_expiry=300  # 5 minutes
            )
            
            # Async HTTP client
            # HTTP/2 disabled for better compatibility with Ollama
            self._http_client = httpx.AsyncClient(
                timeout=1200.0,  # 20 minutes (for long reasoning/tools)
                limits=limits,
                http2=True  # Disabled to prevent connection stability issues
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
        Map model names in response data from Ollama format to client format.
        Also transforms response to be Cursor-compatible.
        
        Args:
            data: Response data with potential model fields
        
        Returns:
            Modified data with display model names and Cursor-compatible format
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
            
            # ============================================================
            # CURSOR COMPATIBILITY: Transform non-standard fields
            # ============================================================
            # Handle 'choices' array for streaming chunks (OpenAI format)
            if 'choices' in data_copy and isinstance(data_copy['choices'], list):
                transformed_choices = []
                for choice in data_copy['choices']:
                    if isinstance(choice, dict):
                        choice_copy = choice.copy()
                        
                        # Handle 'delta' in streaming responses
                        if 'delta' in choice_copy and isinstance(choice_copy['delta'], dict):
                            delta = choice_copy['delta'].copy()
                            
                            # Keep 'reasoning' field as-is for clients that support it
                            # But also check for tool calls in reasoning
                            reasoning = delta.get('reasoning', '')
                            if reasoning and '<|tool_calls_section_begin|>' in reasoning:
                                clean_reasoning, tool_calls_from_reasoning, has_tool_calls = parse_kimi_tool_calls(reasoning)
                                
                                if has_tool_calls:
                                    logger.info(f"[KIMI] Detected {len(tool_calls_from_reasoning)} tool call(s) in reasoning, converting to OpenAI format")
                                    # Update reasoning with clean content
                                    if clean_reasoning:
                                        delta['reasoning'] = clean_reasoning
                                    else:
                                        delta.pop('reasoning', None)
                                    
                                    # Add tool_calls to delta (merge if already exists)
                                    existing_tool_calls = delta.get('tool_calls', [])
                                    delta['tool_calls'] = existing_tool_calls + tool_calls_from_reasoning
                                    
                            # CURSOR COMPATIBILITY: Cursor expects 'reasoning_content' instead of 'reasoning'
                            if 'reasoning' in delta:
                                r_val = delta.pop('reasoning')
                                if r_val: # Only map if not empty
                                    delta['reasoning_content'] = r_val
                            
                            # KIMI TOOL CALL FIX: Convert Kimi's custom tool call format
                            # to OpenAI's standard tool_calls format (in content)
                            content = delta.get('content', '')
                            if content and '<|tool_calls_section_begin|>' in content:
                                clean_content, tool_calls, has_tool_calls = parse_kimi_tool_calls(content)
                                
                                if has_tool_calls:
                                    logger.info(f"[KIMI] Detected {len(tool_calls)} tool call(s) in content, converting to OpenAI format")
                                    # Update delta with clean content and tool_calls
                                    if clean_content:
                                        delta['content'] = clean_content
                                    else:
                                        # If no clean content, remove content field entirely
                                        delta.pop('content', None)
                                    
                                    # Add tool_calls to delta (merge if already exists from reasoning)
                                    existing_tool_calls = delta.get('tool_calls', [])
                                    delta['tool_calls'] = existing_tool_calls + tool_calls
                            
                            choice_copy['delta'] = delta
                        
                        # Handle 'message' in non-streaming responses
                        if 'message' in choice_copy and isinstance(choice_copy['message'], dict):
                            message = choice_copy['message'].copy()
                            
                            # Keep 'reasoning' field as-is for clients that support it
                            # But also check for tool calls in reasoning
                            reasoning = message.get('reasoning', '')
                            if reasoning and '<|tool_calls_section_begin|>' in reasoning:
                                clean_reasoning, tool_calls_from_reasoning, has_tool_calls = parse_kimi_tool_calls(reasoning)
                                
                                if has_tool_calls:
                                    logger.info(f"[KIMI] Detected {len(tool_calls_from_reasoning)} tool call(s) in message reasoning, converting to OpenAI format")
                                    # Update reasoning with clean content
                                    if clean_reasoning:
                                        message['reasoning'] = clean_reasoning
                                    else:
                                        message.pop('reasoning', None)
                                    
                                    # Add tool_calls to message (merge if already exists)
                                    existing_tool_calls = message.get('tool_calls', [])
                                    message['tool_calls'] = existing_tool_calls + tool_calls_from_reasoning
                            
                            # CURSOR COMPATIBILITY: Cursor expects 'reasoning_content' instead of 'reasoning'
                            if 'reasoning' in message:
                                message['reasoning_content'] = message.pop('reasoning')
                            
                            # KIMI TOOL CALL FIX: Convert Kimi's custom tool call format
                            # to OpenAI's standard tool_calls format (non-streaming, in content)
                            content = message.get('content', '')
                            if content and '<|tool_calls_section_begin|>' in content:
                                clean_content, tool_calls, has_tool_calls = parse_kimi_tool_calls(content)
                                
                                if has_tool_calls:
                                    logger.info(f"[KIMI] Detected {len(tool_calls)} tool call(s) in message content, converting to OpenAI format")
                                    # Update message with clean content and tool_calls
                                    if clean_content:
                                        message['content'] = clean_content
                                    else:
                                        message['content'] = None
                                    
                                    # Add tool_calls to message (merge if already exists from reasoning)
                                    existing_tool_calls = message.get('tool_calls', [])
                                    message['tool_calls'] = existing_tool_calls + tool_calls
                            
                            choice_copy['message'] = message
                        
                        transformed_choices.append(choice_copy)
                    else:
                        transformed_choices.append(choice)
                
                data_copy['choices'] = transformed_choices
            
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
                    logger.info(f"[STREAM START] max_tokens: {data.get('max_tokens', 'not set')}, temperature: {data.get('temperature', 'not set')}")
                    
                    try:
                        # Log tools if present
                        if data.get("tools"):
                            logger.info(f"[STREAM START] Tools provided: {[t.get('function', {}).get('name') for t in data['tools']]}")
                        
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
                            
                            # Pending tool calls: key by id (Ollama sends each tool call with index 0 in separate chunks)
                            pending_tool_calls: Dict[str, Dict[str, Any]] = {}  # id -> {idx, id, type, function}
                            
                            prompt_tokens = 0
                            completion_tokens = 0
                            first_chunk_sent = False
                            done_marker_sent = False  # Track if [DONE] was already sent
                            chunk_count = 0
                            total_bytes = 0
                            
                            # KIMI TOOL CALL BUFFER: Accumulate content when tool call section is detected
                            # This is needed because tool call markers can span multiple chunks
                            kimi_content_buffer = ""
                            kimi_buffering_active = False
                            kimi_suspicion_buffer = ""
                            current_model = data.get('model', 'unknown')
                            
                            # State for capturing <think> tag generated natively by models like DeepSeek-R1 or GLM-5
                            in_thinking = False
                            think_suspicion = ""
                            
                            # Only enable Kimi tool call buffering for Kimi models
                            # Other models (Qwen, Gemma, etc.) don't use this format
                            is_kimi_model = 'kimi' in current_model.lower() or 'moonshot' in current_model.lower()
                            
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
                                                    logger.info(f"[OLLAMA IN] {json_str}")
                                                    json_data = json.loads(json_str) 
                                                    # Don't map yet, we'll map below after Kimi check or in normal path
                                                    
                                                    # KIMI TOOL CALL BUFFERING (only for Kimi models):
                                                    # Check if this chunk contains Kimi tool call markers
                                                    # If so, buffer the content until the section is complete
                                                    # Check BOTH content and reasoning fields
                                                    content = ""
                                                    reasoning = ""
                                                    combined_for_detection = ""
                                                    
                                                    if isinstance(json_data, dict) and 'choices' in json_data:
                                                        for choice in json_data.get('choices', []):
                                                            if isinstance(choice, dict):
                                                                delta = choice.get('delta', {})
                                                                if isinstance(delta, dict):
                                                                    content = delta.get('content', '') or ''
                                                                    reasoning = delta.get('reasoning', '') or ''
                                                    
                                                    # Only do Kimi-specific buffering for Kimi models
                                                    if is_kimi_model:
                                                        # Combine content and reasoning for tool call detection
                                                        # Tool calls can appear in either field
                                                        combined_for_detection = content + reasoning
                                                        
                                                        # DEBUG LOG: Show received content (truncated)
                                                        if content:
                                                            logger.info(f"[KIMI DEBUG] Received content chunk: {content[:100]!r}")
                                                        if reasoning:
                                                            logger.info(f"[KIMI DEBUG] Received reasoning chunk: {reasoning[:100]!r}")
                                                        
                                                        # Combine with suspicion buffer if exists
                                                        if kimi_suspicion_buffer:
                                                            logger.info(f"[KIMI DEBUG] Appending suspicion buffer: {kimi_suspicion_buffer!r} to current combined")
                                                            combined_for_detection = kimi_suspicion_buffer + combined_for_detection
                                                            kimi_suspicion_buffer = ""

                                                    # 1. Active Buffering State (only for Kimi)
                                                    if is_kimi_model and kimi_buffering_active:
                                                        kimi_content_buffer += combined_for_detection
                                                        
                                                        # Check if section is complete
                                                        if '<|tool_calls_section_end|>' in kimi_content_buffer:
                                                            logger.info(f"[KIMI] Tool call section complete, processing buffer")
                                                            
                                                            # Parse and convert the buffered content
                                                            clean_content, tool_calls, has_tool_calls = parse_kimi_tool_calls(kimi_content_buffer)
                                                            
                                                            if has_tool_calls:
                                                                logger.info(f"[KIMI] Converted {len(tool_calls)} tool call(s) to OpenAI format")
                                                                
                                                                # Send clean content if any
                                                                if clean_content:
                                                                    # Check if clean_content still contains raw markers (double check)
                                                                    if '<|tool_calls_' in clean_content:
                                                                         # Force remove any remaining markers
                                                                         clean_content = re.sub(r'<\|tool_calls_[^>]+>', '', clean_content)
                                                                    
                                                                    content_chunk = {
                                                                        "id": json_data.get('id', f"chatcmpl-{uuid.uuid4().hex[:12]}"),
                                                                        "object": "chat.completion.chunk",
                                                                        "model": model_mapper.get_display_model_name(current_model),
                                                                        "choices": [{
                                                                            "index": 0,
                                                                            "delta": {"content": clean_content},
                                                                            "finish_reason": None
                                                                        }]
                                                                    }
                                                                    yield b'data: ' + json.dumps(content_chunk, ensure_ascii=False).encode('utf-8') + b'\n\n'
                                                                
                                                                # Send tool calls chunk
                                                                tool_calls_chunk = {
                                                                    "id": json_data.get('id', f"chatcmpl-{uuid.uuid4().hex[:12]}"),
                                                                    "object": "chat.completion.chunk",
                                                                    "model": model_mapper.get_display_model_name(current_model),
                                                                    "choices": [{
                                                                        "index": 0,
                                                                        "delta": {"tool_calls": tool_calls},
                                                                        "finish_reason": None
                                                                    }]
                                                                }
                                                                yield b'data: ' + json.dumps(tool_calls_chunk, ensure_ascii=False).encode('utf-8') + b'\n\n'
                                                                
                                                                # Send finish_reason chunk
                                                                finish_chunk = {
                                                                    "id": json_data.get('id', f"chatcmpl-{uuid.uuid4().hex[:12]}"),
                                                                    "object": "chat.completion.chunk",
                                                                    "model": model_mapper.get_display_model_name(current_model),
                                                                    "choices": [{
                                                                        "index": 0,
                                                                        "delta": {},
                                                                        "finish_reason": "tool_calls"
                                                                    }]
                                                                }
                                                                yield b'data: ' + json.dumps(finish_chunk, ensure_ascii=False).encode('utf-8') + b'\n\n'
                                                                first_chunk_sent = True
                                                            else:
                                                                # No tool calls found after parsing (fake alarm?), yield original buffer
                                                                # But first, try to convert it as regular content
                                                                mapped_data = self._map_model_from_ollama(json.loads(json_str)) # Re-use original mapping logic
                                                                # Override content with full buffer
                                                                if isinstance(mapped_data, dict) and 'choices' in mapped_data:
                                                                    choices = mapped_data.get('choices', [])
                                                                    if choices and len(choices) > 0:
                                                                        choices[0]['delta']['content'] = kimi_content_buffer

                                                                yield b'data: ' + json.dumps(mapped_data, ensure_ascii=False).encode('utf-8') + b'\n\n'
                                                                first_chunk_sent = True
                                                            
                                                            # Reset buffer
                                                            kimi_content_buffer = ""
                                                            kimi_buffering_active = False
                                                            continue
                                                        else:
                                                            # Still buffering, wait for section end
                                                            continue
                                                    
                                                    # 2. Check for Start Marker (only for Kimi)
                                                    if is_kimi_model and '<|tool_calls_section_begin|>' in combined_for_detection:
                                                        kimi_buffering_active = True
                                                        kimi_content_buffer = combined_for_detection
                                                        logger.info(f"[KIMI] Tool call section started, buffering (from {'content' if '<|tool_calls_section_begin|>' in content else 'reasoning'})")
                                                        # Start buffering, don't yield
                                                        continue
                                                        
                                                    # 3. Check for Suspicious Ending (Partial Marker) - only for Kimi
                                                    # If content ends with '<' or '<|' or '<|t' etc., it might be a split marker.
                                                    # The longest marker prefix is about 26 chars.
                                                    # Check if the end of content matches the beginning of the marker
                                                    if is_kimi_model:
                                                        marker_start = "<|tool_calls_section_begin|>"
                                                        is_suspicious = False
                                                        
                                                        # Critical fix: Empty combined is NOT suspicious! 
                                                        # OpenAI sends role-only chunks with empty content first.
                                                        if combined_for_detection:
                                                            # Check suffixes of length 1 to len(marker)-1
                                                            for i in range(1, len(marker_start)):
                                                                if i > len(combined_for_detection):
                                                                    break
                                                                suffix = combined_for_detection[-i:]
                                                                if marker_start.startswith(suffix):
                                                                    is_suspicious = True
                                                                    break
                                                        
                                                        if is_suspicious:
                                                            logger.info(f"[KIMI DEBUG] Combined content is suspicious (possible split marker), buffering: {combined_for_detection!r}")
                                                            kimi_suspicion_buffer = combined_for_detection
                                                            # Don't yield yet, wait for next chunk to confirm
                                                            continue
                                                    
                                                    # Normal processing (no Kimi detection)
                                                    mapped_data = self._map_model_from_ollama(json_data)

                                                    # Extract delta for convenience (with safety check for empty choices)
                                                    delta_obj = {}
                                                    if isinstance(mapped_data, dict) and 'choices' in mapped_data:
                                                        choices = mapped_data.get('choices', [])
                                                        if choices and len(choices) > 0:
                                                            delta_obj = choices[0].get('delta', {})

                                                    # If we had a suspicion buffer that turned out to be false alarm (combined above),
                                                    # we need to make sure we use the COMBINED content, not just the current chunk content.
                                                    # But _map_model_from_ollama uses json_data which only has current chunk.
                                                    # So we manually update the content if we combined buffers.
                                                    if content != (delta.get('content', '') or ''):
                                                        if isinstance(mapped_data, dict) and 'choices' in mapped_data:
                                                            choices = mapped_data.get('choices', [])
                                                            if choices and len(choices) > 0:
                                                                if isinstance(choices[0], dict) and 'delta' in choices[0]:
                                                                    choices[0]['delta']['content'] = content
                                                    
                                                    # Extract token usage if available
                                                    if isinstance(mapped_data, dict) and 'usage' in mapped_data:
                                                        usage = mapped_data['usage']
                                                        prompt_tokens += usage.get('prompt_tokens', 0)
                                                        completion_tokens += usage.get('completion_tokens', 0)
                                                    
                                                    # NOTE: Do NOT skip any chunks! 
                                                    # OpenAI API sends role-only chunks first (content: "")
                                                    # Cursor needs these chunks to understand the response structure
                                                    should_skip = False
                                                    
                                                    # ========= TOOL CALL BUFFERING =========
                                                    if isinstance(mapped_data, dict) and 'choices' in mapped_data and len(mapped_data['choices']) > 0:
                                                        choice = mapped_data['choices'][0]
                                                        delta_obj = choice.get('delta', {})
                                                        content_str = delta_obj.get('content')
                                                        fr = choice.get('finish_reason')
                                                        
                                                        # FIX: Only set has_reasoning if it's truthy to avoid empty reasoning_content: ""
                                                        reasoning_str = delta_obj.get('reasoning_content', "") or ""
                                                        has_reasoning = bool(reasoning_str)
                                                        
                                                        # DYNAMIC <think> TAG INTERCEPTION
                                                        # Some models stream out <think> tags or partial tags.
                                                        if content_str is not None:
                                                            _temp_text = think_suspicion + content_str
                                                            think_suspicion = ""
                                                            content_str = ""
                                                            
                                                            _proc_text = _temp_text
                                                            while _proc_text:
                                                                if not in_thinking:
                                                                    # Try to find start of thinking
                                                                    idx_start = _proc_text.find("<think>")
                                                                    if idx_start != -1:
                                                                        content_str += _proc_text[:idx_start]
                                                                        in_thinking = True
                                                                        _proc_text = _proc_text[idx_start+7:]
                                                                        continue
                                                                    
                                                                    # Also check for unexpected end tag (recovery)
                                                                    idx_end = _proc_text.find("</think>")
                                                                    if idx_end != -1:
                                                                        reasoning_str += _proc_text[:idx_end]
                                                                        has_reasoning = True
                                                                        _proc_text = _proc_text[idx_end+8:]
                                                                        continue

                                                                    # Check for partial tags at the end of string
                                                                    found_partial = False
                                                                    for tag in ["<think>", "</think>"]:
                                                                        for i in range(len(tag)-1, 0, -1):
                                                                            if _proc_text.endswith(tag[:i]):
                                                                                think_suspicion = _proc_text[-i:]
                                                                                content_str += _proc_text[:-i]
                                                                                _proc_text = ""
                                                                                found_partial = True
                                                                                break
                                                                        if found_partial: break
                                                                    
                                                                    if not found_partial:
                                                                        content_str += _proc_text
                                                                        _proc_text = ""
                                                                else:
                                                                    # Try to find end of thinking
                                                                    idx_end = _proc_text.find("</think>")
                                                                    if idx_end != -1:
                                                                        reasoning_str += _proc_text[:idx_end]
                                                                        has_reasoning = True
                                                                        in_thinking = False
                                                                        _proc_text = _proc_text[idx_end+8:]
                                                                        continue

                                                                    # Also check for another start tag (unexpected but handleable)
                                                                    idx_start = _proc_text.find("<think>")
                                                                    if idx_start != -1:
                                                                        reasoning_str += _proc_text[:idx_start]
                                                                        has_reasoning = True
                                                                        in_thinking = True
                                                                        _proc_text = _proc_text[idx_start+7:]
                                                                        continue

                                                                    # Check for partial tags
                                                                    found_partial = False
                                                                    for tag in ["</think>", "<think>"]:
                                                                        for i in range(len(tag)-1, 0, -1):
                                                                            if _proc_text.endswith(tag[:i]):
                                                                                think_suspicion = _proc_text[-i:]
                                                                                reasoning_str += _proc_text[:-i]
                                                                                has_reasoning = True
                                                                                _proc_text = ""
                                                                                found_partial = True
                                                                                break
                                                                        if found_partial: break
                                                                    
                                                                    if not found_partial:
                                                                        reasoning_str += _proc_text
                                                                        has_reasoning = True
                                                                        _proc_text = ""
                                                        
                                                        # Apply mapped content/reasoning back to mapped_data in case we yield it directly
                                                        # It's safer to separate reasoning and content into two chunks if both exist!
                                                        if isinstance(mapped_data, dict) and 'choices' in mapped_data:
                                                            if has_reasoning and content_str:
                                                                # We will handle this by yielding two chunks below!
                                                                pass
                                                            else:
                                                                mapped_data['choices'][0]['delta']['content'] = content_str
                                                                if has_reasoning:
                                                                    mapped_data['choices'][0]['delta']['reasoning_content'] = reasoning_str
                                                        
                                                        # CURSOR FIX: Always yield content/reasoning, even if tool_calls are present
                                                        # This prevents chat from being cut off when tool calls are incomplete/invalid

                                                        # Buffer tool calls for assembly
                                                        # CRITICAL: Ollama/GLM sends each tool call in SEPARATE chunks, all with index 0.
                                                        # Use id as key - same id = accumulate (streaming partial), different id = new tool call.
                                                        if 'tool_calls' in delta_obj and delta_obj['tool_calls']:
                                                            for tc in delta_obj['tool_calls']:
                                                                tc_id = tc.get('id') or f"call_{uuid.uuid4().hex[:8]}"
                                                                tc_func = tc.get('function', {})
                                                                
                                                                if tc_id not in pending_tool_calls:
                                                                    # New tool call - assign next sequential index
                                                                    pending_tool_calls[tc_id] = {
                                                                        "idx": len(pending_tool_calls),
                                                                        "id": tc_id,
                                                                        "type": tc.get('type', 'function'),
                                                                        "function": {"name": tc_func.get('name', ''), "arguments": tc_func.get('arguments', '')}
                                                                    }
                                                                else:
                                                                    # Same id = streaming partial data, accumulate
                                                                    if tc_func.get('name'):
                                                                        pending_tool_calls[tc_id]["function"]["name"] += tc_func['name']
                                                                    if tc_func.get('arguments'):
                                                                        pending_tool_calls[tc_id]["function"]["arguments"] += tc_func['arguments']

                                                        # Yield content/reasoning if present (regardless of tool_calls)
                                                        if content_str or reasoning_str:
                                                            if has_reasoning:
                                                                reasoning_chunk = json.loads(json.dumps(mapped_data))
                                                                r_delta = reasoning_chunk['choices'][0].get('delta', {})
                                                                if first_chunk_sent:
                                                                    r_delta.pop('role', None)
                                                                new_r_delta = {k: v for k, v in r_delta.items() if k not in ('content', 'tool_calls')}
                                                                new_r_delta['reasoning_content'] = reasoning_str
                                                                reasoning_chunk['choices'][0]['delta'] = new_r_delta
                                                                reasoning_chunk['choices'][0]['finish_reason'] = None

                                                                logger.info(f"[PROXY YIELD REASONING] {json.dumps(reasoning_chunk, ensure_ascii=False)}")
                                                                yield b'data: ' + json.dumps(reasoning_chunk, ensure_ascii=False).encode('utf-8') + b'\n\n'
                                                                first_chunk_sent = True

                                                            if content_str:
                                                                content_chunk = json.loads(json.dumps(mapped_data))
                                                                c_delta = content_chunk['choices'][0].get('delta', {})
                                                                if first_chunk_sent:
                                                                    c_delta.pop('role', None)
                                                                new_c_delta = {k: v for k, v in c_delta.items() if k not in ('reasoning_content', 'tool_calls')}
                                                                new_c_delta['content'] = content_str
                                                                content_chunk['choices'][0]['delta'] = new_c_delta
                                                                content_chunk['choices'][0]['finish_reason'] = None

                                                                logger.info(f"[PROXY YIELD CONTENT] {json.dumps(content_chunk, ensure_ascii=False)}")
                                                                yield b'data: ' + json.dumps(content_chunk, ensure_ascii=False).encode('utf-8') + b'\n\n'
                                                                first_chunk_sent = True

                                                        # When the tool call stream finishes, yield the fully assembled tool calls
                                                        # Flushing happens if we receive a finish_reason OR if the stream transitioned away from tool_calls to something else (content etc.)
                                                        flush_tools = False
                                                        if pending_tool_calls:
                                                            if fr in ("tool_calls", "stop"):
                                                                flush_tools = True
                                                            elif not ("tool_calls" in delta_obj and delta_obj["tool_calls"]):
                                                                flush_tools = True

                                                        if flush_tools:
                                                            assembled_calls = []
                                                            for t in sorted(pending_tool_calls.values(), key=lambda x: x["idx"]):
                                                                assembled_calls.append({
                                                                    "index": t["idx"],
                                                                    "id": t["id"],
                                                                    "type": t["type"],
                                                                    "function": {
                                                                        "name": t["function"]["name"],
                                                                        "arguments": t["function"]["arguments"]
                                                                    }
                                                                })
                                                            
                                                            tool_chunk = json.loads(json.dumps(mapped_data))
                                                            tool_chunk["choices"][0]["delta"] = {"tool_calls": assembled_calls}
                                                            tool_chunk["choices"][0]["finish_reason"] = None
                                                            logger.info(f"[PROXY YIELD ASSEMBLED TOOLS] {json.dumps(tool_chunk, ensure_ascii=False)}")
                                                            yield b"data: " + json.dumps(tool_chunk, ensure_ascii=False).encode("utf-8") + b"\n\n"
                                                            first_chunk_sent = True
                                                            pending_tool_calls = {}
                                                            
                                                            # After yielding fully assembled tool calls, yield the finish_reason chunk
                                                            finish_chunk = json.loads(json.dumps(mapped_data))
                                                            f_delta = finish_chunk["choices"][0].get("delta", {})
                                                            if first_chunk_sent:
                                                                f_delta.pop("role", None)
                                                            finish_chunk["choices"][0]["finish_reason"] = fr if fr else "tool_calls"
                                                            logger.info(f"[PROXY YIELD FINISH TOOLS] {json.dumps(finish_chunk, ensure_ascii=False)}")
                                                            yield b"data: " + json.dumps(finish_chunk, ensure_ascii=False).encode("utf-8") + b"\n\n"

                                                    # ========================================

                                                    # Yield remaining content/reasoning (if not already yielded above)
                                                    # This handles cases where content came WITHOUT tool_calls in this chunk
                                                    if not (content_str or reasoning_str):
                                                        if reasoning_str and content_str:
                                                            # Split into two chunks
                                                            r_chunk = json.loads(json.dumps(mapped_data))
                                                            r_delta = r_chunk['choices'][0].get('delta', {})
                                                            # Strip role from subsequent chunks
                                                            has_role = 'role' in r_delta
                                                            if first_chunk_sent:
                                                                r_delta.pop('role', None)
                                                            
                                                            new_r_delta = {k: v for k, v in r_delta.items() if k not in ('content', 'tool_calls')}
                                                            new_r_delta['reasoning_content'] = reasoning_str
                                                            r_chunk['choices'][0]['delta'] = new_r_delta
                                                            
                                                            logger.info(f"[PROXY YIELD REASONING SPLIT] (Role: {has_role and not first_chunk_sent}) {json.dumps(r_chunk, ensure_ascii=False)}")
                                                            yield b'data: ' + json.dumps(r_chunk, ensure_ascii=False).encode('utf-8') + b'\n\n'
                                                            first_chunk_sent = True
                                                            
                                                            c_chunk = json.loads(json.dumps(mapped_data))
                                                            c_delta = c_chunk['choices'][0].get('delta', {})
                                                            # Second chunk is always subsequent
                                                            c_delta.pop('role', None)
                                                            
                                                            new_c_delta = {k: v for k, v in c_delta.items() if k not in ('reasoning_content', 'tool_calls')}
                                                            new_c_delta['content'] = content_str
                                                            c_chunk['choices'][0]['delta'] = new_c_delta
                                                            
                                                            logger.info(f"[PROXY YIELD CONTENT SPLIT] {json.dumps(c_chunk, ensure_ascii=False)}")
                                                            yield b'data: ' + json.dumps(c_chunk, ensure_ascii=False).encode('utf-8') + b'\n\n'
                                                        else:
                                                            # FINAL CLEANUP FOR CURSOR COMPATIBILITY
                                                            if isinstance(mapped_data.get('choices'), list) and len(mapped_data['choices']) > 0:
                                                                choice = mapped_data['choices'][0]
                                                                final_delta = choice.get('delta', {})
                                                                
                                                                # Update with intercepted values
                                                                if has_reasoning:
                                                                    final_delta['reasoning_content'] = reasoning_str
                                                                    if 'reasoning' in final_delta: del final_delta['reasoning']
                                                                if content_str is not None:
                                                                    final_delta['content'] = content_str

                                                                final_fr = choice.get('finish_reason')
                                                                
                                                                # 1. Strip role if already sent
                                                                if first_chunk_sent:
                                                                    final_delta.pop('role', None)
                                                                
                                                                # 2. Handle reasoning vs content
                                                                if final_delta.get('reasoning_content') == "":
                                                                    final_delta.pop('reasoning_content', None)
                                                                
                                                                if 'reasoning_content' in final_delta:
                                                                    # If we have reasoning, usually we don't want empty content
                                                                    if final_delta.get('content') == "":
                                                                        final_delta.pop('content', None)
                                                                
                                                                # 3. Handle stop chunks - OpenAI requires empty delta
                                                                if final_fr is not None:
                                                                    choice['delta'] = {}
                                                            
                                                            logger.info(f"[PROXY YIELD NORMAL] {json.dumps(mapped_data, ensure_ascii=False)}")
                                                            yield b'data: ' + json.dumps(mapped_data, ensure_ascii=False).encode('utf-8') + b'\n\n'
                                                            first_chunk_sent = True
                                                        pass # Removed redundant first_chunk_sent = True
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
                                                
                                                # First map/normalize the model data
                                                # This ensures 'reasoning' field is moved to 'content' if needed
                                                # IMPORTANT: Logic inside _map_model_from_ollama needs to be non-destructive
                                                mapped_data = self._map_model_from_ollama(json_data)
                                                
                                                # Extract content from MAPPED data
                                                content = ""
                                                if isinstance(mapped_data, dict) and 'choices' in mapped_data:
                                                    for choice in mapped_data.get('choices', []):
                                                        if isinstance(choice, dict):
                                                            # Non-streaming 'message' or streaming 'delta'
                                                            delta = choice.get('delta') or choice.get('message') or {}
                                                            if isinstance(delta, dict):
                                                                content = delta.get('content', '') or ''
                                                                
                                                                # DEBUG: Check if tool calls exist natively in mapped data
                                                                if 'tool_calls' in delta:
                                                                    logger.info(f"[NATIVE TOOL CALL] Found native tool_calls in delta: {delta['tool_calls']}")
                                                
                                                # DEBUG LOG: Show received content (normalized)
                                                if content:
                                                     # Only log beginning of content to not spam
                                                     pass
                                                     # logger.info(f"[KIMI DEBUG] Received content chunk: {content[:50]!r}")
                                                elif not content and chunk_count > 1 and chunk_count < 10:
                                                     # Log if content is empty in early chunks (suspicious)
                                                     logger.info(f"[KIMI DEBUG] Empty content in chunk {chunk_count} (Raw keys: {list(json_data.keys())})")
                                                
                                                # Combine with suspicion buffer if exists
                                                if kimi_suspicion_buffer:
                                                    logger.info(f"[KIMI DEBUG] Appending suspicion buffer: {kimi_suspicion_buffer!r} to current content")
                                                    content = kimi_suspicion_buffer + content
                                                    kimi_suspicion_buffer = ""

                                                # 1. Active Buffering State
                                                if kimi_buffering_active:
                                                    kimi_content_buffer += content
                                                    
                                                    # Check if section is complete
                                                    if '<|tool_calls_section_end|>' in kimi_content_buffer:
                                                        logger.info(f"[KIMI] Tool call section complete, processing buffer")
                                                        
                                                        # Parse and convert the buffered content
                                                        clean_content, tool_calls, has_tool_calls = parse_kimi_tool_calls(kimi_content_buffer)
                                                        
                                                        if has_tool_calls:
                                                            logger.info(f"[KIMI] Converted {len(tool_calls)} tool call(s) to OpenAI format")
                                                            
                                                            # Send clean content if any
                                                            if clean_content:
                                                                # Check if clean_content still contains raw markers (double check)
                                                                if '<|tool_calls_' in clean_content:
                                                                     # Force remove any remaining markers
                                                                     clean_content = re.sub(r'<\|tool_calls_[^>]+>', '', clean_content)
                                                                
                                                                content_chunk = {
                                                                    "id": mapped_data.get('id', f"chatcmpl-{uuid.uuid4().hex[:12]}"),
                                                                    "object": "chat.completion.chunk",
                                                                    "model": model_mapper.get_display_model_name(current_model),
                                                                    "choices": [{
                                                                        "index": 0,
                                                                        "delta": {"content": clean_content},
                                                                        "finish_reason": None
                                                                    }]
                                                                }
                                                                
                                                                if is_openai_endpoint:
                                                                    yield b'data: ' + json.dumps(content_chunk, ensure_ascii=False).encode('utf-8') + b'\n\n'
                                                                else:
                                                                    yield json.dumps(content_chunk, ensure_ascii=False).encode('utf-8') + b'\n'
                                                            
                                                            # Send tool calls chunk
                                                            tool_calls_chunk = {
                                                                "id": mapped_data.get('id', f"chatcmpl-{uuid.uuid4().hex[:12]}"),
                                                                "object": "chat.completion.chunk",
                                                                "model": model_mapper.get_display_model_name(current_model),
                                                                "choices": [{
                                                                    "index": 0,
                                                                    "delta": {"tool_calls": tool_calls},
                                                                    "finish_reason": None
                                                                }]
                                                            }
                                                            
                                                            if is_openai_endpoint:
                                                                yield b'data: ' + json.dumps(tool_calls_chunk, ensure_ascii=False).encode('utf-8') + b'\n\n'
                                                            else:
                                                                yield json.dumps(tool_calls_chunk, ensure_ascii=False).encode('utf-8') + b'\n'
                                                            
                                                            # Send finish_reason chunk
                                                            finish_chunk = {
                                                                "id": mapped_data.get('id', f"chatcmpl-{uuid.uuid4().hex[:12]}"),
                                                                "object": "chat.completion.chunk",
                                                                "model": model_mapper.get_display_model_name(current_model),
                                                                "choices": [{
                                                                    "index": 0,
                                                                    "delta": {},
                                                                    "finish_reason": "tool_calls"
                                                                }]
                                                            }
                                                            
                                                            if is_openai_endpoint:
                                                                yield b'data: ' + json.dumps(finish_chunk, ensure_ascii=False).encode('utf-8') + b'\n\n'
                                                            else:
                                                                yield json.dumps(finish_chunk, ensure_ascii=False).encode('utf-8') + b'\n'
                                                                
                                                            first_chunk_sent = True
                                                        else:
                                                            # No tool calls found after parsing (fake alarm?), yield original buffer
                                                            # Update content in mapped_data
                                                            if isinstance(mapped_data, dict) and 'choices' in mapped_data:
                                                                choices = mapped_data.get('choices', [])
                                                                if choices and len(choices) > 0:
                                                                    choices[0]['delta']['content'] = kimi_content_buffer
                                                            
                                                            if is_openai_endpoint:
                                                                yield b'data: ' + json.dumps(mapped_data, ensure_ascii=False).encode('utf-8') + b'\n\n'
                                                            else:
                                                                yield json.dumps(mapped_data, ensure_ascii=False).encode('utf-8') + b'\n'
                                                            first_chunk_sent = True
                                                        
                                                        # Reset buffer
                                                        kimi_content_buffer = ""
                                                        kimi_buffering_active = False
                                                        continue
                                                    else:
                                                        # Still buffering, wait for section end
                                                        continue
                                                
                                                # 2. Check for Start Marker
                                                if '<|tool_calls_section_begin|>' in content:
                                                    kimi_buffering_active = True
                                                    kimi_content_buffer = content
                                                    logger.info(f"[KIMI] Tool call section started, buffering content")
                                                    # Start buffering, don't yield
                                                    continue
                                                    
                                                # 3. Check for Suspicious Ending (Partial Marker)
                                                # Check for BOTH start and end markers splitting
                                                markers_to_check = ["<|tool_calls_section_begin|>", "<|tool_calls_section_end|>"]
                                                is_suspicious = False
                                                
                                                if content:
                                                    for marker in markers_to_check:
                                                        # Check suffixes of length 1 to len(marker)-1
                                                        for i in range(1, len(marker)):
                                                            if i > len(content):
                                                                break
                                                            suffix = content[-i:]
                                                            if marker.startswith(suffix):
                                                                is_suspicious = True
                                                                break
                                                        if is_suspicious:
                                                            break
                                                
                                                if is_suspicious:
                                                    logger.info(f"[KIMI DEBUG] Content is suspicious (possible split marker), buffering: {content[-20:]!r}")
                                                    kimi_suspicion_buffer = content
                                                    # Don't yield yet, wait for next chunk to confirm
                                                    continue
                                                
                                                # Normal processing (no Kimi detection)
                                                if content != (mapped_data.get('message', {}).get('content', '') or ''):
                                                    if 'message' in mapped_data:
                                                        mapped_data['message']['content'] = content
                                                if 'choices' in mapped_data and mapped_data['choices']:
                                                    choices = mapped_data['choices']
                                                    if choices and len(choices) > 0:
                                                        if content != (choices[0].get('delta', {}).get('content', '') or ''):
                                                            choices[0]['delta']['content'] = content
                                                
                                                if isinstance(mapped_data, dict) and 'prompt_eval_count' in mapped_data:
                                                    prompt_tokens += mapped_data.get('prompt_eval_count', 0)
                                                if isinstance(mapped_data, dict) and 'eval_count' in mapped_data:
                                                    completion_tokens += mapped_data.get('eval_count', 0)
                                                
                                                should_skip = False
                                                
                                                # TOOL CALL BUFFERING FOR OLLAMA NDJSON
                                                if isinstance(mapped_data, dict):
                                                    msg = mapped_data.get('message', {})
                                                    if 'tool_calls' in msg and msg['tool_calls']:
                                                        should_skip = True
                                                        for tc in msg['tool_calls']:
                                                            # native ollama might not use index, use function name as hash or index if present
                                                            idx = tc.get('index', len(pending_tool_calls))
                                                            if idx not in pending_tool_calls:
                                                                pending_tool_calls[idx] = {
                                                                    "function": {"name": "", "arguments": ""}
                                                                }
                                                            tc_func = tc.get('function', {})
                                                            if tc_func.get('name'):
                                                                pending_tool_calls[idx]["function"]["name"] += tc_func['name']
                                                            if 'arguments' in tc_func:
                                                                # If arguments is already a dict, stringify it so we can accumulate properly
                                                                # or if it's string, just accumulate.
                                                                arg_val = tc_func['arguments']
                                                                if isinstance(arg_val, dict):
                                                                    arg_val = json.dumps(arg_val)
                                                                pending_tool_calls[idx]["function"]["arguments"] += arg_val
                                                    
                                                    content_str = msg.get('content', '')
                                                    is_done = mapped_data.get('done', False)

                                                    if content_str and should_skip:
                                                        # yield just content
                                                        content_chunk = json.loads(json.dumps(mapped_data))
                                                        content_chunk['message'] = {'role': 'assistant', 'content': content_str}
                                                        content_chunk['done'] = False
                                                        yield json.dumps(content_chunk, ensure_ascii=False).encode('utf-8') + b'\n'
                                                        first_chunk_sent = True
                                                    
                                                    if is_done and pending_tool_calls:
                                                        # parse arguments back into Object as Cursor requires for NDJSON
                                                        formatted_calls = []
                                                        for idx in sorted(pending_tool_calls.keys()):
                                                            t = pending_tool_calls[idx]
                                                            args_str = t['function']['arguments']
                                                            try:
                                                                parsed_args = json.loads(args_str) if args_str else {}
                                                            except json.JSONDecodeError:
                                                                parsed_args = {}
                                                                
                                                            formatted_calls.append({
                                                                "function": {
                                                                    "name": t['function']['name'],
                                                                    "arguments": parsed_args
                                                                }
                                                            })
                                                            
                                                        tool_chunk = json.loads(json.dumps(mapped_data))
                                                        tool_chunk['message'] = {
                                                            'role': 'assistant', 
                                                            'content': '', 
                                                            'tool_calls': formatted_calls
                                                        }
                                                        tool_chunk['done'] = True
                                                        yield json.dumps(tool_chunk, ensure_ascii=False).encode('utf-8') + b'\n'
                                                        first_chunk_sent = True
                                                        pending_tool_calls = {}
                                                        should_skip = True

                                                if not should_skip:
                                                    if is_openai_endpoint:
                                                        yield b'data: ' + json.dumps(mapped_data, ensure_ascii=False).encode('utf-8') + b'\n\n'
                                                    else:
                                                        yield json.dumps(mapped_data, ensure_ascii=False).encode('utf-8') + b'\n'
                                                    first_chunk_sent = True
                                        
                                        except (json.JSONDecodeError, UnicodeDecodeError) as e:
                                            # Log parse errors for debugging
                                            logger.warning(f"[STREAM] JSON parse error: {e}, line: {line[:100]!r}")
                                            continue

                            # FLUSH BUFFER ON STREAM END
                            # This is outside the async for loop
                            if kimi_content_buffer or kimi_suspicion_buffer:
                                logger.info(f"[KIMI DEBUG] Stream ended with remaining buffer. Flushing...")
                                final_content = kimi_content_buffer + kimi_suspicion_buffer
                                
                                # Try to process one last time if it looks like a tool call section
                                if '<|tool_calls_section_begin|>' in final_content:
                                     # Even if end marker is missing, try to parse what we have
                                     clean_content, tool_calls, has_tool_calls = parse_kimi_tool_calls(final_content)
                                     if has_tool_calls:
                                         # Yield remaining tools
                                         tool_calls_chunk = {
                                            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                                            "object": "chat.completion.chunk",
                                            "model": model_mapper.get_display_model_name(current_model),
                                            "choices": [{
                                                "index": 0,
                                                "delta": {"tool_calls": tool_calls},
                                                "finish_reason": "tool_calls"
                                            }]
                                         }
                                         yield b'data: ' + json.dumps(tool_calls_chunk, ensure_ascii=False).encode('utf-8') + b'\n\n'
                                         final_content = clean_content # Update content to be yielded
                                
                                # Yield remaining content if any
                                if final_content:
                                     # Construct a final chunk
                                     final_chunk = {
                                        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                                        "object": "chat.completion.chunk",
                                        "model": model_mapper.get_display_model_name(current_model),
                                        "choices": [{
                                            "index": 0,
                                            "delta": {"content": final_content},
                                            "finish_reason": None # Not verified stop, defer to [DONE]
                                        }]
                                     }
                                     yield b'data: ' + json.dumps(final_chunk, ensure_ascii=False).encode('utf-8') + b'\n\n'

                            if not first_chunk_sent and not done_marker_sent:
                                # If no chunks were sent (very weird), send an empty one to avoid client timeout
                                chunk = {
                                    "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                                    "object": "chat.completion.chunk",
                                    "created": int(time.time()),
                                    "model": model_mapper.get_display_model_name(current_model),
                                    "choices": [
                                        {
                                            "index": 0,
                                            "delta": {"role": "assistant", "content": ""},
                                            "finish_reason": None
                                        }
                                    ]
                                }
                                yield b'data: ' + json.dumps(chunk).encode('utf-8') + b'\n\n'
                                first_chunk_sent = True
                            
                            if not done_marker_sent:
                                yield b'data: [DONE]\n\n'
                                done_marker_sent = True
                            
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
                
                import uuid
                request_id = str(uuid.uuid4())[:8]
                
                return StreamingResponse(
                    stream_generator(),
                    media_type=media_type,
                    headers={
                        # Standard SSE headers
                        "Cache-Control": "no-cache",
                        "X-Accel-Buffering": "no",
                        # Don't override Connection - let client decide
                        # "Connection": "keep-alive",
                    }
                )
            
            # Non-streaming requests with persistent HTTP client
            client = await self._get_http_client()
            if method.upper() == "GET":
                response = await client.get(url)
            elif method.upper() == "POST":
                # Log request data for debugging
                logger.info(f"Sending request to Ollama: {url}")
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


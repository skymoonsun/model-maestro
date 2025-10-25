"""Ollama proxy logic and model name manipulation"""

from typing import Dict, Any, Optional
import httpx
import json
from fastapi import HTTPException, Depends
from fastapi.responses import StreamingResponse

from app.config import get_settings, model_mapper
from app.user_manager import user_manager
from app.auth import get_current_user


class OllamaProxy:
    """Proxy requests to Ollama with model name manipulation"""
    
    def __init__(self):
        self.settings = get_settings()
        self.base_url = self.settings.ollama_base_url
        self._mappings_loaded = False
    
    async def _ensure_mappings_loaded(self):
        """Ensure model mappings are loaded from database"""
        # Only load once at startup, rely on cache invalidation
        if not self._mappings_loaded:
            await model_mapper.ensure_loaded()
            self._mappings_loaded = True
    
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
        Check if user has exceeded their limits
        
        Args:
            username: Username
            request_type: Type of request (generate, chat, embeddings, etc.)
        
        Returns:
            True if user is within limits, False otherwise
        """
        # Get user limits
        user_limit = await user_manager.get_user_limit(username)
        if not user_limit:
            # No limits set, allow request
            return True
        
        # Check request limit
        request_limit = user_limit.get("request_limit")
        if request_limit is not None:
            # Get user's request count for today
            from datetime import datetime, timedelta
            start_of_day = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_day = start_of_day + timedelta(days=1)
            
            token_usage = await user_manager.get_user_token_usage(username, start_of_day, end_of_day)
            if token_usage and token_usage.get("total_requests", 0) >= request_limit:
                return False
        
        # Check token limit
        token_limit = user_limit.get("token_limit")
        if token_limit is not None:
            # Get user's token usage for today
            from datetime import datetime, timedelta
            start_of_day = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_day = start_of_day + timedelta(days=1)
            
            token_usage = await user_manager.get_user_token_usage(username, start_of_day, end_of_day)
            if token_usage and token_usage.get("total_tokens", 0) >= token_limit:
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
        Log user activity for token usage and model access
        
        Args:
            username: Username
            model_name: Model name used
            request_type: Type of request (generate, chat, embeddings, etc.)
            prompt_tokens: Number of prompt tokens used
            completion_tokens: Number of completion tokens used
            total_tokens: Total tokens used
        """
        await user_manager.log_user_activity(
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
        
        try:
            if method.upper() == "POST" and stream:
                # Handle streaming response - client must stay open during streaming
                async def stream_generator():
                    async with httpx.AsyncClient(timeout=600.0) as client:
                        async with client.stream("POST", url, json=data) as resp:
                            if resp.status_code != 200:
                                error_text = await resp.aread()
                                error_msg = error_text.decode()
                                raise HTTPException(
                                    status_code=resp.status_code,
                                    detail=f"Ollama upstream error: {error_msg}"
                                )
                            
                            # Buffer to accumulate partial lines
                            buffer = b""
                            prompt_tokens = 0
                            completion_tokens = 0
                            
                            async for chunk in resp.aiter_raw():
                                if not chunk:
                                    continue
                                
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
                                                    
                                                    yield b'data: ' + json.dumps(mapped_data, ensure_ascii=False).encode('utf-8') + b'\n\n'
                                                else:
                                                    # Pass through [DONE] or empty
                                                    yield line + b'\n'
                                            else:
                                                # Regular Ollama format (NDJSON)
                                                json_data = json.loads(line.decode('utf-8'))
                                                mapped_data = self._map_model_from_ollama(json_data)
                                                
                                                # Extract token usage if available
                                                if isinstance(mapped_data, dict) and 'prompt_eval_count' in mapped_data:
                                                    prompt_tokens += mapped_data.get('prompt_eval_count', 0)
                                                if isinstance(mapped_data, dict) and 'eval_count' in mapped_data:
                                                    completion_tokens += mapped_data.get('eval_count', 0)
                                                
                                                yield json.dumps(mapped_data, ensure_ascii=False).encode('utf-8') + b'\n'
                                        except (json.JSONDecodeError, UnicodeDecodeError):
                                            # If not valid JSON, pass through as-is
                                            yield line + b'\n'
                            
                            # Process any remaining data in buffer
                            if buffer:
                                try:
                                    json_data = json.loads(buffer.decode('utf-8'))
                                    mapped_data = self._map_model_from_ollama(json_data)
                                    
                                    # Extract token usage if available
                                    if isinstance(mapped_data, dict) and 'prompt_eval_count' in mapped_data:
                                        prompt_tokens += mapped_data.get('prompt_eval_count', 0)
                                    if isinstance(mapped_data, dict) and 'eval_count' in mapped_data:
                                        completion_tokens += mapped_data.get('eval_count', 0)
                                    
                                    yield json.dumps(mapped_data, ensure_ascii=False).encode('utf-8') + b'\n'
                                except (json.JSONDecodeError, UnicodeDecodeError):
                                    yield buffer
                            
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
                
                # Return streaming response immediately without buffering
                response = StreamingResponse(
                    stream_generator(),
                    media_type="application/json",
                    headers={
                        "Cache-Control": "no-cache, no-transform",
                        "X-Accel-Buffering": "no"
                    }
                )
                # Remove Transfer-Encoding header to let framework handle it
                return response
            
            # Non-streaming requests
            async with httpx.AsyncClient(timeout=600.0) as client:
                if method.upper() == "GET":
                    response = await client.get(url)
                elif method.upper() == "POST":
                    response = await client.post(url, json=data)
                elif method.upper() == "DELETE":
                    response = await client.delete(url, json=data)
                else:
                    raise HTTPException(status_code=405, detail="Method not allowed")
                
                # Check response status
                if response.status_code >= 400:
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


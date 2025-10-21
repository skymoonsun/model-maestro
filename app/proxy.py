"""Ollama proxy logic and model name manipulation"""

from typing import Dict, Any, Optional
import httpx
import json
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config import get_settings, model_mapper


class OllamaProxy:
    """Proxy requests to Ollama with model name manipulation"""
    
    def __init__(self):
        self.settings = get_settings()
        self.base_url = self.settings.ollama_base_url
    
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
    
    async def proxy_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        stream: bool = False
    ):
        """
        Proxy request to Ollama
        
        Args:
            method: HTTP method (GET, POST, DELETE)
            endpoint: Ollama API endpoint
            data: Request body data
            stream: Whether to stream the response
        
        Returns:
            Response from Ollama (mapped model names)
        """
        url = f"{self.base_url}{endpoint}"
        
        # Map model names in request
        if data:
            data = self._map_model_to_ollama(data)
        
        try:
            if method.upper() == "POST" and stream:
                # Handle streaming response - client must stay open during streaming
                async def stream_generator():
                    async with httpx.AsyncClient(timeout=300.0) as client:
                        async with client.stream("POST", url, json=data) as resp:
                            if resp.status_code != 200:
                                error_text = await resp.aread()
                                raise HTTPException(
                                    status_code=resp.status_code,
                                    detail=f"Ollama error: {error_text.decode()}"
                                )
                            async for chunk in resp.aiter_bytes():
                                if not chunk:
                                    continue
                                # Parse and map model names in streaming chunks
                                try:
                                    # Decode chunk and process each line
                                    text = chunk.decode('utf-8')
                                    lines = text.strip().split('\n')
                                    for line in lines:
                                        if line.strip():
                                            json_data = json.loads(line)
                                            mapped_data = self._map_model_from_ollama(json_data)
                                            yield json.dumps(mapped_data, ensure_ascii=False).encode('utf-8') + b'\n'
                                except json.JSONDecodeError:
                                    # If not valid JSON, pass through as-is
                                    yield chunk
                                except Exception as e:
                                    # Log error but continue streaming
                                    print(f"Stream processing error: {e}")
                                    yield chunk
                
                return StreamingResponse(
                    stream_generator(),
                    media_type="text/event-stream",  # SSE format - Cloudflare daha iyi destekler
                    headers={
                        "Cache-Control": "no-cache, no-transform",
                        "X-Accel-Buffering": "no",  # Nginx buffering'i kapat
                        "Connection": "keep-alive",
                        "Transfer-Encoding": "chunked"
                    }
                )
            
            # Non-streaming requests
            async with httpx.AsyncClient(timeout=300.0) as client:
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
                    return response.text
                
                # Map model names in response
                if endpoint == "/api/tags":
                    response_data = self._map_models_list(response_data)
                else:
                    response_data = self._map_model_from_ollama(response_data)
                
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


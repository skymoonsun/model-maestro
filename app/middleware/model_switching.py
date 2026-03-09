"""
Dynamic Model Switching Middleware

Automatically switches models based on:
- Token limit errors
- Slow response times (>4s timeout)
- API errors and rate limits
- Health check failures

Fallback chain: github-copilot/claude-opus-4.6 → kimi-k2.5 → qwen3.5
"""

import logging
import time
from typing import Optional, List, Dict, Any, Callable
from functools import wraps
import asyncio

logger = logging.getLogger(__name__)

# Default fallback chain
DEFAULT_FALLBACK_CHAIN = [
    "github-copilot/claude-opus-4.6",
    "kimi-k2.5",
    "qwen3.5"
]

# Timeout threshold in seconds
DEFAULT_TIMEOUT_THRESHOLD = 4.0

# Retry delays (exponential backoff)
RETRY_DELAYS = [0.5, 1.0, 2.0]


class FallbackExhaustedError(Exception):
    """Raised when all fallback models have been exhausted"""
    pass


class ModelSwitchingMiddleware:
    """
    Middleware for automatic model switching with fallback support.
    
    Monitors request/response for:
    - Token limit exceeded errors
    - Slow responses (timeout > threshold)
    - API errors (5xx, 429 rate limit)
    - Connection errors
    
    Automatically retries with next model in fallback chain.
    """
    
    def __init__(
        self,
        fallback_chain: Optional[List[str]] = None,
        timeout_threshold: float = DEFAULT_TIMEOUT_THRESHOLD,
        max_retries: int = 3
    ):
        """
        Initialize middleware.
        
        Args:
            fallback_chain: List of models to try in order
            timeout_threshold: Response time threshold in seconds
            max_retries: Maximum retry attempts per model
        """
        self.fallback_chain = fallback_chain or DEFAULT_FALLBACK_CHAIN.copy()
        self.timeout_threshold = timeout_threshold
        self.max_retries = max_retries
        self._model_failure_counts: Dict[str, int] = {}
    
    def get_next_model(self, current_model: str) -> Optional[str]:
        """
        Get next model in fallback chain.
        
        Args:
            current_model: Current model that failed
            
        Returns:
            Next model name or None if chain exhausted
        """
        try:
            current_index = self.fallback_chain.index(current_model)
            if current_index < len(self.fallback_chain) - 1:
                next_model = self.fallback_chain[current_index + 1]
                logger.info(f"🔄 Switching model: {current_model} → {next_model}")
                return next_model
        except ValueError:
            pass
        
        # Current model not in chain, return first model
        if self.fallback_chain:
            return self.fallback_chain[0]
        
        return None
    
    def record_failure(self, model: str, error_type: str):
        """Track model failures for monitoring"""
        if model not in self._model_failure_counts:
            self._model_failure_counts[model] = 0
        self._model_failure_counts[model] += 1
        
        logger.warning(
            f"❌ Model '{model}' failed ({error_type}). "
            f"Total failures: {self._model_failure_counts[model]}"
        )
    
    def should_skip_model(self, model: str) -> bool:
        """
        Check if model should be skipped due to excessive failures.
        
        Args:
            model: Model to check
            
        Returns:
            True if model should be skipped
        """
        # Skip if failed more than max_retries times consecutively
        return self._model_failure_counts.get(model, 0) >= self.max_retries
    
    def is_token_limit_error(self, error_message: str) -> bool:
        """Check if error is related to token limit"""
        token_error_patterns = [
            "token limit",
            "context length",
            "too many tokens",
            "maximum context",
            "context_window_exceeded",
            "model_max_length",
            "input length"
        ]
        
        error_lower = error_message.lower()
        return any(pattern in error_lower for pattern in token_error_patterns)
    
    def is_rate_limit_error(self, status_code: int) -> bool:
        """Check if error is rate limit related"""
        return status_code == 429
    
    def is_server_error(self, status_code: int) -> bool:
        """Check if error is server-side (5xx)"""
        return 500 <= status_code < 600
    
    def should_retry(self, error: Exception, response_time: float) -> bool:
        """
        Determine if request should be retried with same model.
        
        Args:
            error: The exception that occurred
            response_time: Time taken before error
            
        Returns:
            True if should retry with same model
        """
        # Timeout - should switch model
        if response_time > self.timeout_threshold:
            return False
        
        error_str = str(error).lower()
        
        # Token limit - should switch model
        if self.is_token_limit_error(error_str):
            return False
        
        # Connection error - can retry same model
        if "connection" in error_str or "timeout" in error_str:
            return True
        
        # Rate limit - can retry with delay
        if "429" in error_str or "rate limit" in error_str:
            return True
        
        return False
    
    def wrap_model_request(self, func: Callable):
        """
        Decorator to wrap model requests with automatic fallback.
        
        Usage:
            @middleware.wrap_model_request
            async def make_request(model, data):
                ...
        """
        @wraps(func)
        async def wrapper(model: str, *args, **kwargs):
            attempted_models = []
            last_error = None
            
            for attempt in range(len(self.fallback_chain)):
                # Get model for this attempt
                if attempt == 0:
                    current_model = model
                else:
                    current_model = self.get_next_model(attempted_models[-1])
                    if current_model is None:
                        logger.error("🚫 Fallback chain exhausted")
                        raise FallbackExhaustedError(
                            f"All models in fallback chain failed. Last error: {last_error}"
                        )
                
                # Skip models with excessive failures
                if self.should_skip_model(current_model):
                    logger.warning(f"⚠️ Skipping {current_model} (excessive failures)")
                    attempted_models.append(current_model)
                    continue
                
                attempted_models.append(current_model)
                start_time = time.time()
                
                try:
                    logger.info(f"🚀 Attempting request with model: {current_model} (attempt {attempt + 1})")
                    
                    # Execute request
                    result = await func(current_model, *args, **kwargs)
                    
                    # Check response time
                    response_time = time.time() - start_time
                    
                    if response_time > self.timeout_threshold:
                        logger.warning(
                            f"⚠️ Model {current_model} response slow: {response_time:.2f}s "
                            f"(threshold: {self.timeout_threshold}s)"
                        )
                        self.record_failure(current_model, "slow_response")
                        
                        # Continue to next model for better performance
                        last_error = TimeoutError(
                            f"Response time {response_time:.2f}s exceeded threshold {self.timeout_threshold}s"
                        )
                        continue
                    
                    # Success! Reset failure count for this model
                    if current_model in self._model_failure_counts:
                        self._model_failure_counts[current_model] = max(
                            0, self._model_failure_counts[current_model] - 1
                        )
                    
                    logger.info(f"✅ Request successful with {current_model} ({response_time:.2f}s)")
                    return result
                    
                except FallbackExhaustedError:
                    raise
                    
                except Exception as e:
                    response_time = time.time() - start_time
                    error_type = type(e).__name__
                    
                    logger.error(
                        f"❌ Model {current_model} failed after {response_time:.2f}s: "
                        f"{error_type}: {str(e)[:200]}"
                    )
                    
                    self.record_failure(current_model, error_type)
                    last_error = e
                    
                    # Check if we should try next model
                    if self.should_retry(e, response_time):
                        # Retry same model with backoff
                        retry_delay = min(
                            RETRY_DELAYS[attempt % len(RETRY_DELAYS)],
                            2.0
                        )
                        logger.info(f"⏳ Retrying {current_model} in {retry_delay}s...")
                        await asyncio.sleep(retry_delay)
                        # Don't add to attempted_models, will retry same model
                        attempted_models.pop()
                        continue
                    
                    # Try next model
                    continue
            
            # All models failed
            logger.error(f"🚫 All {len(attempted_models)} models failed: {attempted_models}")
            raise FallbackExhaustedError(
                f"All fallback models failed. Attempted: {attempted_models}. Last error: {last_error}"
            )
        
        return wrapper
    
    def get_status(self) -> Dict[str, Any]:
        """Get middleware status for monitoring"""
        return {
            "enabled": True,
            "fallback_chain": self.fallback_chain,
            "timeout_threshold": self.timeout_threshold,
            "max_retries": self.max_retries,
            "failure_counts": self._model_failure_counts.copy(),
            "healthy_models": [
                m for m in self.fallback_chain
                if self._model_failure_counts.get(m, 0) < self.max_retries
            ]
        }


# Global middleware instance
model_switching_middleware = ModelSwitchingMiddleware()

from functools import wraps
import time
from typing import List, Optional

FALLBACK_MODELS = [
    "github-copilot/claude-opus-4.6",  # Primary
    "ollama/kimi-k2.5:latest",         # Fallback 1
    "ollama/qwen3.5:397b"              # Fallback 2
]

TIMEOUT_THRESHOLD = 4.0  # seconds

def with_model_fallback(max_retries: int = 3):
    """
    Model switching decorator with fallback chain.
    Token limit, timeout, or error'da bir sonraki modele geçer.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            errors = []
            for i, model in enumerate(FALLBACK_MODELS):
                try:
                    start = time.time()
                    kwargs['model'] = model
                    result = func(*args, **kwargs)
                    elapsed = time.time() - start
                    
                    if elapsed > TIMEOUT_THRESHOLD:
                        print(f"WARNING: Response slow ({elapsed:.2f}s) for {model}")
                    
                    return result
                except Exception as e:
                    errors.append((model, str(e)))
                    print(f"Model {model} failed: {e}, trying next...")
                    continue
            
            raise Exception(f"All models failed: {errors}")
        return wrapper
    return decorator

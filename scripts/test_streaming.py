#!/usr/bin/env python3
"""
Test script to debug streaming issues with Ollama and the proxy.
Run this inside the Docker container or locally.
"""

import asyncio
import httpx
import json
import sys
import time

# Configuration
OLLAMA_URL = "http://172.17.0.1:11434"  # Ollama URL from Docker
PROXY_URL = "http://localhost:8000"  # Proxy URL
TEST_MODEL = "gemini-3-pro-preview:latest"

async def test_ollama_direct():
    """Test streaming directly to Ollama"""
    print("\n" + "="*60)
    print("TEST 1: Direct Ollama Streaming Test")
    print("="*60)
    
    url = f"{OLLAMA_URL}/v1/chat/completions"
    payload = {
        "model": TEST_MODEL,
        "messages": [{"role": "user", "content": "Say hello in one word"}],
        "stream": True
    }
    
    print(f"URL: {url}")
    print(f"Payload: {json.dumps(payload, indent=2)}")
    print("-"*60)
    
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            start_time = time.time()
            async with client.stream("POST", url, json=payload) as resp:
                print(f"Status: {resp.status_code}")
                print(f"Headers: {dict(resp.headers)}")
                print("-"*60)
                
                chunk_count = 0
                total_bytes = 0
                
                async for chunk in resp.aiter_raw():
                    chunk_count += 1
                    total_bytes += len(chunk)
                    elapsed = time.time() - start_time
                    
                    print(f"[{elapsed:.2f}s] Chunk {chunk_count}: {len(chunk)} bytes")
                    print(f"  Content: {chunk[:200]!r}")
                    if len(chunk) > 200:
                        print(f"  ... ({len(chunk) - 200} more bytes)")
                    print()
                
                elapsed = time.time() - start_time
                print(f"\n{'='*60}")
                print(f"RESULT: {chunk_count} chunks, {total_bytes} bytes, {elapsed:.2f}s")
                print("="*60)
                
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")


async def test_ollama_non_streaming():
    """Test non-streaming to Ollama"""
    print("\n" + "="*60)
    print("TEST 2: Direct Ollama Non-Streaming Test")
    print("="*60)
    
    url = f"{OLLAMA_URL}/v1/chat/completions"
    payload = {
        "model": TEST_MODEL,
        "messages": [{"role": "user", "content": "Say hello in one word"}],
        "stream": False
    }
    
    print(f"URL: {url}")
    print(f"Payload: {json.dumps(payload, indent=2)}")
    print("-"*60)
    
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            start_time = time.time()
            resp = await client.post(url, json=payload)
            elapsed = time.time() - start_time
            
            print(f"Status: {resp.status_code}")
            print(f"Headers: {dict(resp.headers)}")
            print(f"Response ({len(resp.text)} chars):")
            print(resp.text[:1000])
            if len(resp.text) > 1000:
                print(f"... ({len(resp.text) - 1000} more chars)")
            
            print(f"\n{'='*60}")
            print(f"RESULT: {resp.status_code}, {len(resp.text)} chars, {elapsed:.2f}s")
            print("="*60)
            
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")


async def test_model_list():
    """Test listing models from Ollama"""
    print("\n" + "="*60)
    print("TEST 3: Ollama Model List")
    print("="*60)
    
    url = f"{OLLAMA_URL}/v1/models"
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url)
            print(f"Status: {resp.status_code}")
            
            if resp.status_code == 200:
                data = resp.json()
                models = data.get("data", [])
                print(f"Found {len(models)} models:")
                for model in models[:10]:
                    print(f"  - {model.get('id', 'unknown')}")
                if len(models) > 10:
                    print(f"  ... and {len(models) - 10} more")
            else:
                print(f"Error: {resp.text}")
                
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")


async def main():
    print("Ollama Streaming Debug Script")
    print(f"Ollama URL: {OLLAMA_URL}")
    print(f"Test Model: {TEST_MODEL}")
    
    # Test model list first
    await test_model_list()
    
    # Test non-streaming (simpler, should work)
    await test_ollama_non_streaming()
    
    # Test streaming
    await test_ollama_direct()


if __name__ == "__main__":
    asyncio.run(main())

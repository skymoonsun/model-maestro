#!/usr/bin/env python3
"""Test Google v1internal generateContent directly with saved OAuth token."""

import asyncio
import json
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import async_session_maker
from app.repositories.node_repository import NodeRepository
from app.google_auth import ensure_fresh_token, build_v1internal_headers
from app.google_proxy import wrap_v1internal_request, V1_INTERNAL_BASE_URLS

import httpx


async def test_generate_content():
    async with async_session_maker() as session:
        repo = NodeRepository(session)
        nodes = await repo.list_active()
        antigravity_nodes = [n for n in nodes if n.node_type == 'antigravity']

        if not antigravity_nodes:
            print("No active antigravity nodes found!")
            return

        node = antigravity_nodes[0]
        print(f"Node: {node.name} (id={node.id})")
        print(f"Project ID: {node.project_id or '(none)'}")
        print(f"OAuth tokens present: {bool(node.oauth_tokens)}")

        if not node.oauth_tokens:
            print("No OAuth tokens saved!")
            return

        # Refresh token if needed
        access_token = await ensure_fresh_token(
            node.oauth_tokens,
            client_id="",
            client_secret="",
        )
        print(f"Access token valid (refreshed if needed)")

        # Build request body
        inner_body = {
            "contents": [
                {"role": "user", "parts": [{"text": "hi"}]}
            ],
            "generationConfig": {
                "temperature": 1.0,
                "topP": 1.0,
                "topK": 40,
                "maxOutputTokens": 100,
            },
        }

        wrapped = wrap_v1internal_request(
            inner_body,
            project_id=node.project_id or "",
            mapped_model="gemini-2.5-pro",
        )
        wrapped["requestType"] = "generate_content"

        headers = build_v1internal_headers(access_token)
        if node.project_id:
            headers["x-goog-user-project"] = node.project_id

        body_bytes = json.dumps(wrapped, ensure_ascii=False).encode("utf-8")

        print(f"\n--- Testing generateContent ---")
        print(f"Body: {json.dumps(wrapped, indent=2)[:500]}...")

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            http2=True,
        ) as client:
            for base_url in V1_INTERNAL_BASE_URLS:
                url = f"{base_url}:generateContent"
                print(f"\nTrying: {url}")
                try:
                    response = await client.post(
                        url,
                        headers=headers,
                        content=body_bytes,
                    )
                    print(f"Status: {response.status_code}")
                    print(f"Response: {response.text[:1000]}")

                    if response.status_code == 200:
                        print("\n✅ SUCCESS! generateContent works!")
                        return
                    elif response.status_code == 403 and "x-goog-user-project" in headers:
                        print("403 with project header, retrying without...")
                        headers.pop("x-goog-user-project", None)
                        response2 = await client.post(
                            url,
                            headers=headers,
                            content=body_bytes,
                        )
                        print(f"Retry Status: {response2.status_code}")
                        print(f"Retry Response: {response2.text[:1000]}")
                        if response2.status_code == 200:
                            print("\n✅ SUCCESS without project header!")
                            return

                except Exception as e:
                    print(f"Error: {e}")

        print("\n❌ All endpoints failed")


if __name__ == "__main__":
    asyncio.run(test_generate_content())

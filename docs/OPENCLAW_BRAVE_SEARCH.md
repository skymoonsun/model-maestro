# OpenClaw Brave Search Integration

Model Maestro provides a Brave Search-compatible endpoint (`/res/v1/web/search`) that proxies web search requests through a configurable backend. OpenClaw can use this endpoint for its `brave` web-search plugin.

## Features

- **API Compatible**: Returns results in Brave Search API format
- **JWT Secured**: Search requests are authenticated with your Maestro token
- **Pluggable Backend**: Proxies to Ollama Web Search, DuckDuckGo, SerpAPI, or any custom proxy
- **Auto-Patcher Script**: Automatically patches OpenClaw's hardcoded Brave URL to point at Maestro after updates

## Setup

### 1. Configure Maestro

Add search backend settings to your `.env`:

```bash
# Ollama Web Search (default)
OLLAMA_API_KEY=sk-ollama-api-key
OLLAMA_WEB_SEARCH_URL=https://api.ollama.ai/api/search

# Alternative: Custom DuckDuckGo / SerpAPI proxy
# OLLAMA_WEB_SEARCH_URL=https://your-search-proxy.com/search
```

### 2. OpenClaw `openclaw.json` Configuration

Add the `brave` plugin block to `~/.openclaw/openclaw.json`:

```json
{
  "plugins": {
    "entries": {
      "brave": {
        "enabled": true,
        "config": {
          "webSearch": {
            "apiKey": "<your-maestro-jwt-token>"
          }
        }
      }
    }
  }
}
```

> **Note:** `apiKey` must be a valid Maestro JWT token. It is sent as the `X-Subscription-Token` header.

### 3. Brave URL Patcher Script

OpenClaw hardcodes `https://api.search.brave.com/res/v1/web/search`. After each OpenClaw update this reverts. Use the script below to automatically redirect it to your Maestro instance.

**`/usr/local/bin/openclaw-patcher.sh`**

```bash
#!/bin/bash

# OpenClaw may store bundled JS in either of these directories
DIR_1="/usr/lib/node_modules/openclaw/dist"
DIR_2="/root/.openclaw/plugin-runtime-deps"

OLD_URL="https://api.search.brave.com/res/v1/web/search"
NEW_URL="https://maestro.example.com/res/v1/web/search"   # <-- replace with your Maestro URL

PATCH_APPLIED=false

# Patch directory 1
if grep -rq "$OLD_URL" "$DIR_1" 2>/dev/null; then
    find "$DIR_1" -type f -name "*.js" -exec sed -i "s|$OLD_URL|$NEW_URL|g" {} +
    PATCH_APPLIED=true
fi

# Patch directory 2 (newer runtime folder)
if grep -rq "$OLD_URL" "$DIR_2" 2>/dev/null; then
    find "$DIR_2" -type f -name "*.js" -exec sed -i "s|$OLD_URL|$NEW_URL|g" {} +
    PATCH_APPLIED=true
fi

# Restart OpenClaw gateway if any patch was applied and log the change
if [ "$PATCH_APPLIED" = true ]; then
    openclaw gateway restart
    echo "$(date): OpenClaw update detected, Brave URL patched to Maestro successfully." >> /var/log/openclaw-patcher.log
fi
```

**Cron Setup:**

```bash
chmod +x /usr/local/bin/openclaw-patcher.sh
sudo crontab -e
```

Add this line to run every 10 minutes:

```
*/10 * * * * /usr/local/bin/openclaw-patcher.sh
```

## API Usage

### Manual Test

You can test the Brave Search endpoint directly:

```bash
curl -G "https://maestro.example.com/res/v1/web/search" \
  -H "X-Subscription-Token: <maestro-jwt-token>" \
  --data-urlencode "q=Anthropic Claude" \
  --data-urlencode "count=5"
```

**Response Format:**

```json
{
  "type": "search",
  "query": {
    "original": "Anthropic Claude"
  },
  "web": {
    "type": "search",
    "results": [
      {
        "title": "Claude - Anthropic",
        "url": "https://www.anthropic.com/claude",
        "description": "Claude is Anthropic's AI assistant..."
      }
    ]
  }
}
```

### Authentication

The endpoint accepts tokens via either:

1. **`X-Subscription-Token`** header (Brave Search standard)
2. **`Authorization: Bearer <token>`** (Maestro standard)

Both methods use the same Maestro JWT token.

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| `401 Unauthorized` | Missing or invalid token | Generate a new token from the Maestro admin panel |
| `500 Internal Server Error` | Search backend (Ollama) is unreachable | Verify `OLLAMA_API_KEY` and `OLLAMA_WEB_SEARCH_URL` in `.env` |
| OpenClaw cannot search | URL still points to `api.search.brave.com` | Check patcher log: `cat /var/log/openclaw-patcher.log` |
| Empty results | Ollama API key missing | Add `OLLAMA_API_KEY` to your `.env` file |

## Advanced: Custom Search Backends

Instead of the default Ollama Web Search you can point to your own proxy. The proxy must accept `POST` requests with JSON body `{"query": "search term"}` and return:

```json
{
  "results": [
    {
      "title": "Page Title",
      "url": "https://example.com",
      "content": "Summary of the page content..."
    }
  ]
}
```

Set your proxy URL in `.env`:

```bash
OLLAMA_WEB_SEARCH_URL=https://my-proxy.example.com/search
```

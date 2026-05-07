# IDE Integration Guide

Model Maestro acts as a unified LLM gateway for AI-powered IDEs. This guide covers setup for **Claude Code**, **OpenClaw** and **Cursor**.

## Table of Contents

- [Claude Code](#claude-code)
- [OpenClaw](#openclaw)
- [Cursor](#cursor)

---

## Claude Code

Claude Code supports custom base URLs via environment variables.

### Environment Variables

```bash
export ANTHROPIC_BASE_URL=https://maestro.example.com/claude/
export ANTHROPIC_AUTH_TOKEN=<your-maestro-jwt-token>
export ANTHROPIC_API_KEY=<your-maestro-jwt-token>
export ANTHROPIC_MODEL=<mapped-model-name>
export ANTHROPIC_SMALL_FAST_MODEL=<fast-model-name>
```

### Launch

```bash
claude
```

### Notes

- `ANTHROPIC_BASE_URL` must end with `/claude/` to hit the Claude-compatible proxy endpoint.
- Both `ANTHROPIC_AUTH_TOKEN` and `ANTHROPIC_API_KEY` should contain your Maestro JWT token.
- `ANTHROPIC_MODEL` and `ANTHROPIC_SMALL_FAST_MODEL` should be **display names** from your model mappings (e.g. `kimi-k2.6:latest`).
- Create a user token via the admin panel (`/users`) if you do not have one.

---

## OpenClaw

OpenClaw reads provider configuration from `~/.openclaw/openclaw.json`.

### Configuration

Add a `maestro` provider block under `models.providers`:

```json
{
  "models": {
    "mode": "merge",
    "providers": {
      "maestro": {
        "baseUrl": "http://localhost:8000/openclaw",
        "apiKey": "<your-maestro-jwt-token>",
        "api": "openai-completions",
        "timeoutSeconds": 600,
        "models": [
          {
            "id": "kimi-k2.6:latest",
            "name": "kimi-k2.6:latest",
            "reasoning": true,
            "input": ["text", "image"],
            "cost": {
              "input": 0,
              "output": 0,
              "cacheRead": 0,
              "cacheWrite": 0
            },
            "contextWindow": 262144,
            "maxTokens": 8192,
            "api": "openai-completions"
          }
        ]
      }
    }
  }
}
```

### Notes

- `baseUrl` points to Maestro's OpenClaw-compatible endpoint (`/openclaw`).
- `apiKey` is your Maestro JWT token.
- `models[].id` must match a mapped display name in Maestro.
- Set `timeoutSeconds` high enough for large model responses (default `600`).
- Add as many model entries as you have mappings.

---

## Cursor

Cursor supports custom OpenAI base URLs for all LLM requests.

### Requirements

- **Cursor Pro** — Custom API keys require a Pro subscription.
- **Publicly accessible URL** — Cursor's servers must reach your Maestro instance. Use a VPS, reverse proxy (e.g. Nginx + Cloudflare Tunnel) or deploy with a public domain.

### Setup

1. Open **Cursor Settings**.
2. Go to the **Models** tab.
3. Enable **OpenAI** under API Keys.
4. Paste your Maestro JWT token into the **OpenAI API Key** field.
5. Enable **Override OpenAI Base URL**.
6. Enter your public Maestro URL with the `/cursor` suffix:

   ```
   https://maestro.example.com/cursor
   ```

### Notes

- The `/cursor` endpoint proxies OpenAI-compatible requests with Cursor-specific model-name mapping.
- Because Cursor runs in the cloud, `http://localhost:8000` will not work unless you expose it via a tunnel (e.g. Cloudflare Tunnel, ngrok, or a VPS).
- If you have multiple models mapped, Cursor will list them automatically after setting the base URL.

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| `401 Unauthorized` | JWT token expired or wrong | Refresh token in admin panel; verify `Authorization: Bearer <token>` header |
| `404 Not Found` | Wrong base URL path | Ensure Claude uses `/claude/`, OpenClaw uses `/openclaw`, Cursor uses `/cursor` |
| Model not listed | No mapping created | Create a model mapping in the admin panel (AI Models > Mappings) |
| Connection refused (Cursor) | Localhost not reachable from Cursor cloud | Expose Maestro via public URL or tunnel |
| Empty model list in Cursor | `/cursor` endpoint not returning models | Check Maestro logs; ensure model discovery succeeded for at least one node |

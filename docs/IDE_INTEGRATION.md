# IDE Integration Guide

Model Maestro acts as a unified LLM gateway for AI-powered IDEs. This guide covers setup for **Claude Code**, **VS Code** (Claude Code & Kilo Code extensions), **OpenClaw**, **Cursor** and **Grafana Assistant**.

## Table of Contents

- [Claude Code](#claude-code)
- [VS Code](#vs-code)
- [OpenClaw](#openclaw)
- [Cursor](#cursor)
- [Grafana Assistant](#grafana-assistant)

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

## VS Code

The **Claude Code** extension for VS Code supports custom environment variables via `settings.json`.

### Configuration

Open your VS Code user settings JSON (macOS: `~/Library/Application\ Support/Code/User/settings.json`) and add the `claudeCode.environmentVariables` array:

```json
{
    "claudeCode.environmentVariables": [
        {
            "name": "ANTHROPIC_BASE_URL",
            "value": "https://maestro.example.com/claude/"
        },
        {
            "name": "ANTHROPIC_API_KEY",
            "value": "<your-maestro-jwt-token>"
        },
        {
            "name": "ANTHROPIC_AUTH_TOKEN",
            "value": "<your-maestro-jwt-token>"
        },
        {
            "name": "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY",
            "value": "1"
        }
    ]
}
```

### Notes

- `ANTHROPIC_BASE_URL` must end with `/claude/` to hit the Claude-compatible proxy endpoint.
- Both `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` should contain your Maestro JWT token.
- `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1` enables automatic model discovery from the Maestro gateway — models mapped in Maestro will appear in the extension's model picker.
- The Claude Code extension in VS Code uses the same `/claude/` endpoint as the CLI tool.
- Create a user token via the admin panel (`/users`) if you do not have one.

---

## Kilo Code

**Kilo Code** is a VS Code extension that supports connecting to custom providers via an OpenAI-compatible API.

### Setup

1. Install the **Kilo Code** extension from the VS Code marketplace.
2. Open the Kilo Code settings and navigate to the **Providers** page.
3. Click **Custom provider connect**.
4. Fill in the connection fields:

   | Field | Value |
   |---|---|
   | Provider ID | `maestro` |
   | Display name | `Maestro` |
   | Base URL | `https://maestro.example.com/v1` |
   | API Key | Your Maestro JWT token |

### Notes

- The `/v1` endpoint proxies OpenAI-compatible requests directly.
- Kilo Code does not require model-name mapping — it uses the models exposed by the `/v1/models` endpoint.
- Ensure your Maestro instance has at least one model mapping configured so `/v1/models` returns a non-empty list.

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

### Web Search (Brave Search Plugin)

OpenClaw can use Maestro as its Brave Search backend for web search capabilities. Add the `brave` plugin entry to `~/.openclaw/openclaw.json`:

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

Then use the [patcher script](../docs/OPENCLAW_BRAVE_SEARCH.md#3-brave-url-patcher-script) to redirect OpenClaw's hardcoded Brave URL (`https://api.search.brave.com`) to your Maestro instance. See [`docs/OPENCLAW_BRAVE_SEARCH.md`](../docs/OPENCLAW_BRAVE_SEARCH.md) for full setup including cron configuration.

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

## Grafana Assistant

Grafana Assistant is an official Grafana plugin that adds AI-powered features inside Grafana dashboards. Model Maestro provides a fully compatible backend for this plugin.

### Requirements

- **Grafana >= 13.0.0-0**
- **Grafana Assistant plugin** installed from the plugin catalog

### Installation

1. In Grafana, go to **Administration > Plugins and data > Plugins**.
2. Search for and install **Grafana Assistant**.
3. After installation, navigate to the plugin's details page (`/plugins/grafana-assistant-app`).
4. Switch to the **Connection** tab.

### Configuration (Method 1 — Browser Script, Recommended)

The plugin validates that the Backend URL ends with `.grafana.net` by default. Use the bypass script to override this restriction:

1. On the plugin's Connection page, click **Manual configuration** to expand the form.
2. Open your browser's DevTools (`F12 > Console`).
3. Paste the contents of [`docs/grafana-assistant-bypass.js`](grafana-assistant-bypass.js) and press **Enter**.
4. The script will:
   - Auto-expand the form if needed
   - Fill the fields automatically
   - Bypass domain validation
   - Click **Save & connect**

**Script default values:**

| Field | Default Value | Description |
|---|---|---|
| Backend URL | `http://localhost:8000/grafana/assistant` | Point this at your Maestro Grafana Assistant endpoint |
| Instance ID | `1622805` | Fixed Grafana Assistant instance ID |
| API Token | `API_KEY` | Replace this with a valid Maestro JWT token before running the script |

> **Important:** Edit the `CONFIG.apiToken` value in the script to your actual Maestro JWT token before pasting it into the console.

### Configuration (Method 2 — Reverse Proxy)

If you prefer not to use the browser script, expose your Maestro instance through a reverse proxy that serves it on a `.grafana.net` subdomain:

```
https://maestro.example.com/grafana/assistant  →  http://localhost:8000/grafana/assistant
```

Then fill the Connection form manually with:

| Field | Value |
|---|---|
| Backend URL | `https://maestro.example.com/grafana/assistant` |
| Instance ID | `1622805` |
| API Token | Your Maestro JWT token |

### Notes

- The `/grafana/assistant` endpoint supports streaming chat, chat history, model config and infrastructure discovery.
- Configure the default model for Grafana Assistant in the Maestro admin panel under **Grafana Config**.
- Request logs from Grafana Assistant are tagged with source `grafana` in the admin panel.

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| `401 Unauthorized` | JWT token expired or wrong | Refresh token in admin panel; verify `Authorization: Bearer <token>` header |
| `404 Not Found` | Wrong base URL path | Ensure Claude uses `/claude/`, OpenClaw uses `/openclaw`, Cursor uses `/cursor`, Grafana uses `/grafana/assistant` |
| Model not listed | No mapping created | Create a model mapping in the admin panel (AI Models > Mappings) |
| Connection refused (Cursor) | Localhost not reachable from Cursor cloud | Expose Maestro via public URL or tunnel |
| Empty model list in Cursor | `/cursor` endpoint not returning models | Check Maestro logs; ensure model discovery succeeded for at least one node |
| Grafana "Domain not allowed" | Backend URL does not end with `.grafana.net` | Use the bypass script (Method 1) or reverse proxy with `.grafana.net` domain (Method 2) |

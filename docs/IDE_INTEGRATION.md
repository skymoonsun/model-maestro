# IDE Integration Guide

Model Maestro acts as a unified LLM gateway for AI-powered IDEs. This guide covers setup for **Claude Code**, **Claude Desktop (Cowork 3P)**, **VS Code** (Claude Code & Kilo Code extensions), **OpenClaw**, **Cursor** and **Grafana Assistant**.

## Table of Contents

- [Claude Code](#claude-code)
- [Claude Desktop (Cowork 3P)](#claude-desktop-cowork-3p)
- [VS Code](#vs-code)
- [OpenClaw](#openclaw)
- [Cursor](#cursor)
- [Grafana Assistant](#grafana-assistant)

---

## Claude Code

Claude Code supports custom base URLs via environment variables. Official reference: [Claude Code environment variables](https://code.claude.com/docs/en/env-vars) and [Model configuration](https://code.claude.com/docs/en/model-config).

### Minimum setup (Maestro gateway)

```bash
export ANTHROPIC_BASE_URL=https://maestro.example.com/claude/
export ANTHROPIC_AUTH_TOKEN=<your-maestro-jwt-token>
export ANTHROPIC_API_KEY=<your-maestro-jwt-token>
export CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1
```

### Pin models per tier (recommended)

Claude Code uses **different models for different jobs**. Maestro forwards the model id the client sends; it does **not** guess or remap unknown names. Configure the client so every tier uses a name that exists in Maestro (model mapping + node catalog after sync).

| Job | What triggers it | Env to control |
|-----|------------------|----------------|
| Main chat (`/model opus`) | `ANTHROPIC_MODEL` or `/model` | `ANTHROPIC_DEFAULT_OPUS_MODEL` |
| Sonnet alias | `sonnet` in picker | `ANTHROPIC_DEFAULT_SONNET_MODEL` |
| **Background / fast tasks** | Haiku alias, lightweight tools | `ANTHROPIC_DEFAULT_HAIKU_MODEL` |
| **Subagents** (Explore, Plan, “summarize project”, etc.) | Spawns separate agent | `CLAUDE_CODE_SUBAGENT_MODEL` |

If you only configure Opus but leave Haiku/subagent defaults, Claude Code may send Anthropic-native ids (e.g. `haiku-4-5-20251001`) that your gateway nodes do not expose — you will see 404 or upstream errors until you pin those env vars.

Example (replace with names from **Admin → Models** / your mappings and synced node catalogs):

```bash
export ANTHROPIC_DEFAULT_OPUS_MODEL=<maestro-mapped-model>
export ANTHROPIC_DEFAULT_SONNET_MODEL=<maestro-mapped-model>
export ANTHROPIC_DEFAULT_HAIKU_MODEL=<maestro-mapped-model>
export CLAUDE_CODE_SUBAGENT_MODEL=<maestro-mapped-model>

# Optional: default for new sessions (overridden by /model for that session)
export ANTHROPIC_MODEL=<maestro-mapped-model>
```

Maestro’s `/claude/v1/messages` handler strips the `claude-` prefix before routing. Use the same spelling as in your Maestro mapping and provider node catalog.

`ANTHROPIC_SMALL_FAST_MODEL` is **deprecated**; use `ANTHROPIC_DEFAULT_HAIKU_MODEL` instead.

This applies regardless of backend (Ollama, vLLM, Bedrock, Antigravity, etc.): the client must request model ids Maestro knows how to route.

### VS Code / settings.json example

```json
{
  "claudeCode.environmentVariables": [
    { "name": "ANTHROPIC_BASE_URL", "value": "https://maestro.example.com/claude/" },
    { "name": "ANTHROPIC_API_KEY", "value": "<your-maestro-jwt-token>" },
    { "name": "ANTHROPIC_AUTH_TOKEN", "value": "<your-maestro-jwt-token>" },
    { "name": "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY", "value": "1" },
    { "name": "ANTHROPIC_DEFAULT_OPUS_MODEL", "value": "<maestro-mapped-model>" },
    { "name": "ANTHROPIC_DEFAULT_HAIKU_MODEL", "value": "<maestro-mapped-model>" },
    { "name": "CLAUDE_CODE_SUBAGENT_MODEL", "value": "<maestro-mapped-model>" }
  ]
}
```

Optional: restrict the picker with `availableModels` in `~/.claude/settings.json` (aliases `opus`, `sonnet`, `haiku` — see [Model configuration](https://code.claude.com/docs/en/model-config#restrict-model-selection)).

### Launch

```bash
claude
```

### Notes

- `ANTHROPIC_BASE_URL` must end with `/claude/` to hit the Claude-compatible proxy endpoint.
- Both `ANTHROPIC_AUTH_TOKEN` and `ANTHROPIC_API_KEY` should contain your Maestro JWT token.
- `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1` fills the `/model` picker from Maestro’s model list (`GET /claude/v1/models`). Only models you expose there (and grant to the user) should be selected.
- **Do not rely on Maestro to invent model ids** — configure `ANTHROPIC_DEFAULT_HAIKU_MODEL` and `CLAUDE_CODE_SUBAGENT_MODEL` to mapped names your nodes actually serve.
- Create a user token via the admin panel (`/users`) if you do not have one.

---

## Claude Desktop (Cowork 3P)

Claude Desktop in **third-party inference** mode (`inferenceProvider: gateway`) talks to Maestro’s Anthropic-compatible API. Official docs: [Using an LLM gateway](https://claude.com/docs/cowork/3p/gateway) and [Configuration reference](https://claude.com/docs/cowork/3p/configuration).

### Why the picker shows fewer models than the API

This is usually **not** “Maestro lost models”. Claude Desktop **1.6259+** filters models in two ways ([anthropics/claude-code#56990](https://github.com/anthropics/claude-code/issues/56990)):

| Check | Rule |
|-------|------|
| **Keyword / alias** | Id must match `^(sonnet\|opus\|haiku)(-[\d.]+)?$` **or** contain `claude`, `sonnet`, `opus`, `haiku`, `anthropic`. |
| **Substring blocklist** | Id must **not** contain competitor names, e.g. `kimi`, `qwen`, `deepseek`, `gpt`, `gemini`, `mimo`, … |

So `claude-kimi-k2.6:latest` fails even though it starts with `claude-` — the blocklist matches `kimi` inside the id. Desktop is not checking “real Anthropic model”; it is pattern + blocklist matching.

Maestro with `X-Maestro-Client: claude-desktop` uses **opaque ids** so Desktop never sees blocked substrings:

| Field | Example |
|-------|---------|
| `id` | `claude-maestro-a1b2c3d4e5f6` (SHA-256 of routing name, first 12 hex chars) |
| `display_name` | `google/codegemma-7b` (real name, unchanged) |

Mappings are stored in Redis (`maestro:claude_desktop_route:{hash}`) and resolved on `POST /v1/messages` **only when** `X-Maestro-Client: claude-desktop` is sent. Without that header, `claude-maestro-…` is not resolved (plain **404 model not found**). Re-run model discovery after deploy so Desktop picks up new ids.

**Test connection** is a separate issue: Desktop sends a tiny `POST /v1/messages` probe. If routing ignores `preferred_node_ids` and hits the wrong node (e.g. Ollama), you get 404 even when the model exists on Antigravity.

### Recommended Desktop setup

| Field | Value |
|-------|--------|
| Inference provider | `gateway` |
| Gateway base URL | `https://maestro.example.com/claude` (no trailing slash required; Desktop appends `/v1/...`) |
| Gateway API key | Maestro JWT |
| Model discovery | `true` (default) **or** set an explicit `inferenceModels` list (see below) |

Anthropic recommends an **explicit model list** when discovery is noisy — picker shows exactly what you configure, and you can pin Haiku for sub-agents. Example `inferenceModels` (JSON string in managed config):

```json
[
  "claude-maestro-2bf4c98a7478",
  "claude-maestro-8f3a2b1c4d5e"
]
```

Use opaque **`id`** values from model discovery (with `X-Maestro-Client` set), not raw catalog names like `kimi-k2.6:latest`.

### Custom header: Desktop mode on Maestro

In the Desktop config UI (**Custom inference headers** / `inferenceCustomHeaders`), add:

```json
{
  "X-Maestro-Client": "claude-desktop"
}
```

Maestro then returns **Anthropic-shaped `capabilities`** on `GET /claude/v1/models` so Desktop is more likely to show gateway models in the picker. Logs include `client=desktop`.

Accepted values: `claude-desktop`, `cowork`, `desktop`.

This header is sent on **every** inference request (including model discovery and test connection). It enables Desktop-safe **model id aliasing** (not just capabilities). It does not replace correct routing or node sync.

**Manual model entry** must use the opaque **`id`** from discovery (e.g. `claude-maestro-8f3a2b1c4d5e`), not the raw catalog name (`google/codegemma-7b`). The picker label comes from `display_name`.

### Verify

1. Developer → Configure third-party inference → **Test model discovery** — should list models from Maestro.
2. Gateway logs: `[Claude] User … requesting model list (…, client=desktop)`.
3. Compare raw API: `curl -H "Authorization: Bearer $TOKEN" -H "X-Maestro-Client: claude-desktop" https://maestro.example.com/claude/v1/models | jq '.data | length'`

API reference (endpoints, header behavior, 404 rules): [API.md — Claude API](API.md#claude-desktop-header).

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
        },
        {
            "name": "ANTHROPIC_DEFAULT_OPUS_MODEL",
            "value": "<maestro-mapped-model>"
        },
        {
            "name": "ANTHROPIC_DEFAULT_HAIKU_MODEL",
            "value": "<maestro-mapped-model>"
        },
        {
            "name": "CLAUDE_CODE_SUBAGENT_MODEL",
            "value": "<maestro-mapped-model>"
        }
    ]
}
```

### Notes

- `ANTHROPIC_BASE_URL` must end with `/claude/` to hit the Claude-compatible proxy endpoint.
- Both `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` should contain your Maestro JWT token.
- `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1` enables automatic model discovery from the Maestro gateway — models mapped in Maestro will appear in the extension's model picker.
- Pin `ANTHROPIC_DEFAULT_HAIKU_MODEL` and `CLAUDE_CODE_SUBAGENT_MODEL` like the CLI section above; subagents and background tasks use those tiers.
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
| Main chat works, “summarize” / subagent fails | Claude Code uses Haiku/subagent model, not main Opus | Set `ANTHROPIC_DEFAULT_HAIKU_MODEL` and `CLAUDE_CODE_SUBAGENT_MODEL` to a synced Antigravity model id |
| `haiku-4-5-…` 404 on Antigravity | ID is Claude Code default, not in your sync catalog | Pin Haiku/subagent env vars; run **Sync Models**; add Maestro mapping if display name differs |
| Desktop shows few models | Client-side id/capability filter + blocklist | Add `X-Maestro-Client: claude-desktop`; use opaque ids from discovery |
| `claude-maestro-…` works in curl but not Desktop | Missing custom header on Desktop | Set `inferenceCustomHeaders` in Cowork 3P config |
| `claude-maestro-…` 404 without header | Opaque ids only resolve with Desktop header | Expected — use real model name for Claude Code/curl, or send the header |
| Desktop test connection 404 | Wrong node (e.g. Ollama instead of Antigravity) | Ensure model group preferred node is healthy; sync node catalog; deploy routing pin fix |
| Grafana "Domain not allowed" | Backend URL does not end with `.grafana.net` | Use the bypass script (Method 1) or reverse proxy with `.grafana.net` domain (Method 2) |

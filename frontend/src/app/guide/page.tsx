'use client';

import { useState } from 'react';
import {
    Copy,
    CheckCircle,
    AlertTriangle,
    ExternalLink,
} from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { toast } from 'sonner';

// ── Shared UI ────────────────────────────────────────────────

function CopyButton({ text }: { text: string }) {
    const [copied, setCopied] = useState(false);

    const handleCopy = () => {
        navigator.clipboard.writeText(text).then(() => {
            setCopied(true);
            toast.success('Copied to clipboard');
            setTimeout(() => setCopied(false), 2000);
        });
    };

    return (
        <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6 absolute top-2 right-2 opacity-50 hover:opacity-100"
            onClick={handleCopy}
        >
            {copied ? <CheckCircle className="h-3.5 w-3.5 text-green-400" /> : <Copy className="h-3.5 w-3.5" />}
        </Button>
    );
}

function CodeBlock({ code }: { code: string }) {
    return (
        <div className="relative rounded-md bg-muted p-3 mt-3">
            <CopyButton text={code} />
            <pre className="text-xs font-mono text-foreground whitespace-pre-wrap break-all pr-8">
                <code>{code}</code>
            </pre>
        </div>
    );
}

// ── Data ─────────────────────────────────────────────────────

const ideIntegrations = [
    {
        id: 'claude-code',
        name: 'Claude Code',
        logo: '/guide/claude.svg',
        color: 'text-amber-400',
        bg: 'bg-amber-400/10 border-amber-400/30',
        description: 'Anthropic CLI tool with custom base URL support.',
        steps: [
            'Set environment variables before launching.',
            'ANTHROPIC_BASE_URL must end with /claude/.',
            'ANTHROPIC_AUTH_TOKEN and ANTHROPIC_API_KEY should both contain your Maestro JWT token.',
            'Pin Opus, Sonnet, Haiku, and subagent tiers to Maestro-mapped model names (see env vars below).',
            'ANTHROPIC_SMALL_FAST_MODEL is deprecated — use ANTHROPIC_DEFAULT_HAIKU_MODEL instead.',
        ],
        code: `export ANTHROPIC_BASE_URL=https://maestro.example.com/claude/
export ANTHROPIC_AUTH_TOKEN=<your-maestro-jwt-token>
export ANTHROPIC_API_KEY=<your-maestro-jwt-token>
export CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1

# Pin every tier to a model Maestro routes (mapping + node catalog)
export ANTHROPIC_DEFAULT_OPUS_MODEL=<maestro-mapped-model>
export ANTHROPIC_DEFAULT_SONNET_MODEL=<maestro-mapped-model>
export ANTHROPIC_DEFAULT_HAIKU_MODEL=<maestro-mapped-model>
export CLAUDE_CODE_SUBAGENT_MODEL=<maestro-mapped-model>
export ANTHROPIC_MODEL=<maestro-mapped-model>

claude`,
        note: 'Maestro forwards the model id Claude Code sends; it does not guess unknown names. Subagents and background tasks use Haiku/subagent env vars — configure them or requests may fail with 404.',
    },
    {
        id: 'claude-desktop',
        name: 'Claude Desktop (Cowork 3P)',
        logo: '/guide/claude.svg',
        color: 'text-amber-300',
        bg: 'bg-amber-300/10 border-amber-300/30',
        description:
            'Claude Desktop app in third-party inference mode — routes chat through Maestro as an Anthropic-compatible gateway.',
        steps: [
            'Enable Developer Mode: Help → Troubleshooting → Enable Developer Mode.',
            'Developer → Configure third-party inference.',
            'Set Inference provider to Gateway.',
            'Gateway base URL: https://maestro.example.com/claude (Maestro JWT as API key).',
            'Under Custom inference headers, add X-Maestro-Client: claude-desktop (required).',
            'Apply locally and restart Desktop. Run Test model discovery.',
            'Pick models from the list — use the opaque id (claude-maestro-…), not the raw Ollama name.',
        ],
        code: `Inference provider:     gateway
Gateway base URL:       https://maestro.example.com/claude
Gateway API key:        <your-maestro-jwt-token>
Gateway auth scheme:    bearer

Custom inference headers (inferenceCustomHeaders):
{
  "X-Maestro-Client": "claude-desktop"
}

Optional explicit model list (inferenceModels) — use opaque ids from discovery:
[
  "claude-maestro-2bf4c98a7478",
  "claude-maestro-8f3a2b1c4d5e"
]`,
        note: 'Without X-Maestro-Client, Maestro does not resolve claude-maestro-{hash} ids (404 model not found). Discovery returns opaque ids so Desktop accepts Kimi/Qwen/Gemma models; display_name shows the real catalog name.',
    },
    {
        id: 'openclaw',
        name: 'OpenClaw',
        logo: '/guide/openclaw.svg',
        color: 'text-cyan-400',
        bg: 'bg-cyan-400/10 border-cyan-400/30',
        description: 'Universal AI IDE plugin reading provider config from ~/.openclaw/openclaw.json.',
        steps: [
            'Add a maestro provider block under models.providers.',
            'baseUrl points to /openclaw endpoint.',
            'models[].id must match a mapped display name in Maestro.',
        ],
        code: `{
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
            "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 },
            "contextWindow": 262144,
            "maxTokens": 8192,
            "api": "openai-completions"
          }
        ]
      }
    }
  }
}`,
    },
    {
        id: 'cursor',
        name: 'Cursor',
        logo: '/guide/cursor.svg',
        color: 'text-purple-400',
        bg: 'bg-purple-400/10 border-purple-400/30',
        description: 'AI-native code editor using custom OpenAI base URLs.',
        steps: [
            'Requires Cursor Pro subscription for custom API keys.',
            'Cursor servers must reach your Maestro instance publicly (VPS / tunnel).',
            'Settings → Models → OpenAI → Override Base URL.',
        ],
        code: `OpenAI API Key:  <your-maestro-jwt-token>
Override OpenAI Base URL:  https://maestro.example.com/cursor`,
        note: 'The /cursor endpoint proxies OpenAI-compatible requests with Cursor-specific model-name mapping.',
    },
    {
        id: 'grafana',
        name: 'Grafana Assistant',
        logo: '/guide/grafana.svg',
        color: 'text-orange-400',
        bg: 'bg-orange-400/10 border-orange-400/30',
        description: 'Official Grafana plugin for AI-powered dashboards.',
        steps: [
            'Requires Grafana >= 13.0.0 and Grafana Assistant plugin installed.',
            'Navigate to the plugin Connection page.',
            'Use the browser bypass script or a .grafana.net reverse proxy.',
        ],
        code: `Backend URL:   http://localhost:8000/grafana/assistant
Instance ID:   1622805
API Token:     <your-maestro-jwt-token>`,
        note: 'See docs/grafana-assistant-bypass.js for the browser console bypass script.',
    },
    {
        id: 'vscode',
        name: 'VS Code',
        logo: '/guide/vscode.svg',
        color: 'text-blue-500',
        bg: 'bg-blue-500/10 border-blue-500/30',
        description: 'VS Code extensions for AI-powered coding with Model Maestro.',
        extensions: [
            {
                extId: 'claude-code-ext',
                extName: 'Claude Code',
                extLogo: '/guide/claude.svg',
                extSteps: [
                    'Open VS Code settings JSON (Cmd+Shift+P → Preferences: Open User Settings JSON).',
                    'Add claudeCode.environmentVariables array with your Maestro endpoint.',
                    'Set ANTHROPIC_BASE_URL to end with /claude/.',
                    'Enable CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY for auto model listing.',
                    'Set ANTHROPIC_DEFAULT_* and CLAUDE_CODE_SUBAGENT_MODEL to Maestro-mapped model names.',
                ],
                extCode: `{
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
}`,
                extNote: 'Pin Haiku and subagent models to names Maestro routes. ANTHROPIC_SMALL_FAST_MODEL is deprecated; use ANTHROPIC_DEFAULT_HAIKU_MODEL.',
            },
            {
                extId: 'kilo-code-ext',
                extName: 'Kilo Code',
                extLogo: '/guide/kilocode.svg',
                extSteps: [
                    'Install the Kilo Code extension from the VS Code marketplace.',
                    'Open Kilo Code settings → Providers page.',
                    'Click "Custom provider connect".',
                    'Fill in the fields with your Maestro endpoint and token.',
                ],
                extCode: `Provider ID:   maestro
Display name:  Maestro
Base URL:      https://maestro.example.com/v1
API Key:       <your-maestro-jwt-token>`,
                extNote: 'The /v1 endpoint proxies OpenAI-compatible requests. Kilo Code uses this endpoint directly without model-name mapping.',
            },
        ],
    },
];

const providers = [
    {
        id: 'ollama',
        name: 'Ollama',
        logo: '/guide/ollama.svg',
        color: 'text-blue-400',
        bg: 'bg-blue-400/10 border-blue-400/30',
        type: 'ollama',
        endpoints: ['/api/chat', '/api/generate', '/api/tags', '/api/show', '/api/pull'],
        features: ['Local inference', 'Model pull/push', 'Per-node warmup', 'Health checks'],
    },
    {
        id: 'vllm',
        name: 'vLLM',
        logo: '/guide/vllm.svg',
        color: 'text-purple-400',
        bg: 'bg-purple-400/10 border-purple-400/30',
        type: 'vllm',
        endpoints: ['/v1/chat/completions', '/v1/models', '/v1/embeddings'],
        features: ['OpenAI-compatible API', 'Streaming SSE', 'Bearer auth forwarding', 'max_model_len discovery'],
    },
    {
        id: 'antigravity',
        name: 'Antigravity',
        logo: '/guide/antigravity.svg',
        color: 'text-green-400',
        bg: 'bg-green-400/10 border-green-400/30',
        type: 'antigravity',
        endpoints: ['/v1internal'],
        features: ['Google OAuth 2.0', 'Gemini & Claude models', 'Auto token refresh', 'Project ID support'],
    },
    {
        id: 'bedrock',
        name: 'AWS Bedrock',
        logo: '/guide/bedrock.svg',
        color: 'text-orange-400',
        bg: 'bg-orange-400/10 border-orange-400/30',
        type: 'bedrock',
        endpoints: ['/bedrock'],
        features: ['AWS Converse API', 'Image input', 'Streaming', 'STS session tokens'],
    },
];

// ── Page ─────────────────────────────────────────────────────

export default function GuidePage() {
    return (
        <div className="w-full">
            <div className="mb-8">
                <h1 className="text-3xl font-bold tracking-tight">Integration Guide</h1>
                <p className="text-muted-foreground mt-2 max-w-2xl">
                    Connect your favorite IDEs and tools to Model Maestro — including Claude Code, Claude Desktop (Cowork 3P), VS Code, Cursor, and OpenClaw. Supports Ollama, vLLM, Antigravity, and Bedrock providers out of the box.
                </p>
            </div>

            <Tabs defaultValue="ide" className="w-full">
                <TabsList className="mb-6">
                    <TabsTrigger value="ide">IDE Integrations</TabsTrigger>
                    <TabsTrigger value="providers">Supported Providers</TabsTrigger>
                </TabsList>

                <TabsContent value="ide" className="space-y-6">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        {ideIntegrations.map((ide) => (
                            <Card key={ide.id} className={`border ${ide.bg} ${ide.id === 'vscode' ? 'md:col-span-2' : ''}`}>
                                <CardHeader className="pb-3">
                                    <div className="flex items-center gap-3">
                                        <div className={`p-2 rounded-md bg-muted ${ide.color}`}>
                                            <img src={ide.logo} alt={ide.name} className="h-6 w-6 object-contain" />
                                        </div>
                                        <div>
                                            <CardTitle className="text-base">{ide.name}</CardTitle>
                                            <CardDescription className="text-xs">{ide.description}</CardDescription>
                                        </div>
                                    </div>
                                </CardHeader>
                                <CardContent className="space-y-4 pt-0">
                                    {ide.extensions ? (
                                        ide.extensions.map((ext) => (
                                            <div key={ext.extId} className="border-t border-border/50 first:border-t-0 pt-4 first:pt-0">
                                                <div className="flex items-center gap-2 mb-3">
                                                    <img src={ext.extLogo} alt={ext.extName} className="h-5 w-5 object-contain" />
                                                    <h4 className="text-sm font-semibold">{ext.extName}</h4>
                                                </div>
                                                <div className="space-y-2">
                                                    {ext.extSteps.map((step, i) => (
                                                        <div key={i} className="flex items-start gap-2 text-sm">
                                                            <CheckCircle className="h-4 w-4 mt-0.5 shrink-0 text-muted-foreground" />
                                                            <span className="text-muted-foreground">{step}</span>
                                                        </div>
                                                    ))}
                                                </div>
                                                <CodeBlock code={ext.extCode} />
                                                {ext.extNote && (
                                                    <div className="flex items-start gap-2 text-xs text-amber-400 bg-amber-400/5 rounded-md p-2">
                                                        <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                                                        <span>{ext.extNote}</span>
                                                    </div>
                                                )}
                                            </div>
                                        ))
                                    ) : (
                                        <>
                                            <div className="space-y-2">
                                                {ide.steps.map((step, i) => (
                                                    <div key={i} className="flex items-start gap-2 text-sm">
                                                        <CheckCircle className="h-4 w-4 mt-0.5 shrink-0 text-muted-foreground" />
                                                        <span className="text-muted-foreground">{step}</span>
                                                    </div>
                                                ))}
                                            </div>
                                            <CodeBlock code={ide.code} />
                                            {ide.note && (
                                                <div className="flex items-start gap-2 text-xs text-amber-400 bg-amber-400/5 rounded-md p-2">
                                                    <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                                                    <span>{ide.note}</span>
                                                </div>
                                            )}
                                        </>
                                    )}
                                </CardContent>
                            </Card>
                        ))}
                    </div>
                </TabsContent>

                <TabsContent value="providers" className="space-y-6">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        {providers.map((provider) => (
                            <Card key={provider.id} className={`border ${provider.bg}`}>
                                <CardHeader className="pb-3">
                                    <div className="flex items-center justify-between">
                                        <div className="flex items-center gap-3">
                                            <div className={`p-2 rounded-md bg-muted ${provider.color}`}>
                                                <img src={provider.logo} alt={provider.name} className="h-6 w-6 object-contain" />
                                            </div>
                                            <div>
                                                <CardTitle className="text-base">{provider.name}</CardTitle>
                                                <CardDescription className="text-xs">
                                                    Node type: <Badge variant="outline" className="text-[10px]">{provider.type}</Badge>
                                                </CardDescription>
                                            </div>
                                        </div>
                                    </div>
                                </CardHeader>
                                <CardContent className="space-y-4 pt-0">
                                    <div>
                                        <p className="text-xs font-medium text-muted-foreground mb-2">API Endpoints</p>
                                        <div className="flex flex-wrap gap-1.5">
                                            {provider.endpoints.map((ep) => (
                                                <Badge key={ep} variant="secondary" className="text-[10px] font-mono">
                                                    {ep}
                                                </Badge>
                                            ))}
                                        </div>
                                    </div>
                                    <div>
                                        <p className="text-xs font-medium text-muted-foreground mb-2">Features</p>
                                        <div className="flex flex-wrap gap-2">
                                            {provider.features.map((f) => (
                                                <div key={f} className="flex items-center gap-1 text-xs text-muted-foreground">
                                                    <CheckCircle className="h-3 w-3 text-green-400" />
                                                    {f}
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                </CardContent>
                            </Card>
                        ))}
                    </div>
                </TabsContent>
            </Tabs>

            <div className="mt-12 border-t pt-6">
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <ExternalLink className="h-4 w-4" />
                    <span>
                        For detailed configuration, Claude Desktop header behavior, and troubleshooting, see{' '}
                        <a
                            href="https://github.com/skymoonsun/model-maestro/blob/main/docs/IDE_INTEGRATION.md"
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-foreground underline underline-offset-4 hover:text-primary"
                        >
                            docs/IDE_INTEGRATION.md
                        </a>
                        {' '}and{' '}
                        <a
                            href="https://github.com/skymoonsun/model-maestro/blob/main/docs/API.md#claude-desktop-header"
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-foreground underline underline-offset-4 hover:text-primary"
                        >
                            docs/API.md (Claude API)
                        </a>
                    </span>
                </div>
            </div>
        </div>
    );
}

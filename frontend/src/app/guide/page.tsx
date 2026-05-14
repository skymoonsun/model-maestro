'use client';

import { useState } from 'react';
import {
    Terminal,
    Wrench,
    MousePointerClick,
    BarChart3,
    Server,
    Zap,
    Globe,
    Cloud,
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

function CodeBlock({ code, lang = 'bash' }: { code: string; lang?: string }) {
    return (
        <div className="relative rounded-md bg-muted p-3 mt-3">
            <CopyButton text={code} />
            <pre className="text-xs font-mono text-foreground overflow-x-auto pr-8">
                <code>{code}</code>
            </pre>
        </div>
    );
}

const ideIntegrations = [
    {
        id: 'claude-code',
        name: 'Claude Code',
        icon: Terminal,
        color: 'text-amber-400',
        bg: 'bg-amber-400/10 border-amber-400/30',
        description: 'Anthropic CLI tool with custom base URL support.',
        steps: [
            'Set environment variables before launching.',
            'ANTHROPIC_BASE_URL must end with /claude/.',
            'ANTHROPIC_AUTH_TOKEN and ANTHROPIC_API_KEY should both contain your Maestro JWT token.',
        ],
        code: `export ANTHROPIC_BASE_URL=https://maestro.example.com/claude/
export ANTHROPIC_AUTH_TOKEN=<your-maestro-jwt-token>
export ANTHROPIC_API_KEY=<your-maestro-jwt-token>
export ANTHROPIC_MODEL=<mapped-model-name>
export ANTHROPIC_SMALL_FAST_MODEL=<fast-model-name>

claude`,
    },
    {
        id: 'openclaw',
        name: 'OpenClaw',
        icon: Wrench,
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
        icon: MousePointerClick,
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
        icon: BarChart3,
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
];

const providers = [
    {
        id: 'ollama',
        name: 'Ollama',
        icon: Server,
        color: 'text-blue-400',
        bg: 'bg-blue-400/10 border-blue-400/30',
        type: 'ollama',
        endpoints: ['/api/chat', '/api/generate', '/api/tags', '/api/show', '/api/pull'],
        features: ['Local inference', 'Model pull/push', 'Per-node warmup', 'Health checks'],
    },
    {
        id: 'vllm',
        name: 'vLLM',
        icon: Zap,
        color: 'text-purple-400',
        bg: 'bg-purple-400/10 border-purple-400/30',
        type: 'vllm',
        endpoints: ['/v1/chat/completions', '/v1/models', '/v1/embeddings'],
        features: ['OpenAI-compatible API', 'Streaming SSE', 'Bearer auth forwarding', 'max_model_len discovery'],
    },
    {
        id: 'antigravity',
        name: 'Antigravity',
        icon: Globe,
        color: 'text-green-400',
        bg: 'bg-green-400/10 border-green-400/30',
        type: 'antigravity',
        endpoints: ['/v1internal'],
        features: ['Google OAuth 2.0', 'Gemini & Claude models', 'Auto token refresh', 'Project ID support'],
    },
    {
        id: 'bedrock',
        name: 'AWS Bedrock',
        icon: Cloud,
        color: 'text-orange-400',
        bg: 'bg-orange-400/10 border-orange-400/30',
        type: 'bedrock',
        endpoints: ['/bedrock'],
        features: ['AWS Converse API', 'Image input', 'Streaming', 'STS session tokens'],
    },
];

export default function GuidePage() {
    return (
        <div className="max-w-5xl mx-auto">
            <div className="mb-8">
                <h1 className="text-3xl font-bold tracking-tight">Integration Guide</h1>
                <p className="text-muted-foreground mt-2 max-w-2xl">
                    Connect your favorite IDEs and tools to Model Maestro. Supports Ollama, vLLM, Antigravity, and Bedrock providers out of the box.
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
                            <Card key={ide.id} className={`border ${ide.bg}`}>
                                <CardHeader className="pb-3">
                                    <div className="flex items-center gap-3">
                                        <div className={`p-2 rounded-md bg-muted ${ide.color}`}>
                                            <ide.icon className="h-5 w-5" />
                                        </div>
                                        <div>
                                            <CardTitle className="text-base">{ide.name}</CardTitle>
                                            <CardDescription className="text-xs">{ide.description}</CardDescription>
                                        </div>
                                    </div>
                                </CardHeader>
                                <CardContent className="space-y-4 pt-0">
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
                                                <provider.icon className="h-5 w-5" />
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
                        For detailed configuration examples and troubleshooting, see{' '}
                        <a
                            href="https://github.com/skymoonsun/model-maestro/blob/main/docs/IDE_INTEGRATION.md"
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-foreground underline underline-offset-4 hover:text-primary"
                        >
                            docs/IDE_INTEGRATION.md
                        </a>
                    </span>
                </div>
            </div>
        </div>
    );
}

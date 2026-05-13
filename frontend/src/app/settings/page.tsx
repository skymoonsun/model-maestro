'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { systemConfigApi, tunnelApi, type TunnelConfig, type TunnelStatus } from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from 'sonner';
import { useState, useEffect } from 'react';
import { Save, Settings, Globe, Play, Square } from 'lucide-react';

export default function SettingsPage() {
    const queryClient = useQueryClient();
    const { data: config, isLoading } = useQuery({
        queryKey: ['system-config'],
        queryFn: systemConfigApi.get,
    });

    const { data: tunnelStatus, refetch: refetchTunnelStatus } = useQuery({
        queryKey: ['tunnel-status'],
        queryFn: tunnelApi.getStatus,
        refetchInterval: 5000,
    });

    const [editValues, setEditValues] = useState<Record<string, Record<string, string>>>({});

    useEffect(() => {
        // eslint-disable-next-line react-hooks/set-state-in-effect
        if (config) setEditValues(JSON.parse(JSON.stringify(config)));
    }, [config]);

    const updateMutation = useMutation({
        mutationFn: (data: Record<string, Record<string, string>>) => systemConfigApi.update(data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['system-config'] });
            toast.success('Settings updated');
        },
        onError: (err) => toast.error(err.message),
    });

    const tunnelConfigMutation = useMutation({
        mutationFn: (data: TunnelConfig) => tunnelApi.updateConfig(data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['system-config'] });
            toast.success('Tunnel config saved');
        },
        onError: (err) => toast.error(err.message),
    });

    const startTunnelMutation = useMutation({
        mutationFn: () => tunnelApi.start(),
        onSuccess: (data: TunnelStatus) => {
            refetchTunnelStatus();
            toast.success(data.public_url ? `Tunnel started: ${data.public_url}` : 'Tunnel started');
        },
        onError: (err) => toast.error(err.message),
    });

    const stopTunnelMutation = useMutation({
        mutationFn: () => tunnelApi.stop(),
        onSuccess: () => {
            refetchTunnelStatus();
            toast.success('Tunnel stopped');
        },
        onError: (err) => toast.error(err.message),
    });

    const handleSave = () => {
        const grouped: Record<string, Record<string, string>> = {};
        Object.entries(editValues).forEach(([category, values]) => {
            if (category === 'ollama_unsupported_params') return;
            grouped[category] = {};
            Object.entries(values).forEach(([key, value]) => {
                grouped[category][key] = value;
            });
        });
        updateMutation.mutate(grouped);
    };

    const tunnelConfig = (editValues.tunnel || {}) as unknown as TunnelConfig;
    const isCloudflare = tunnelConfig.provider === 'cloudflare';
    const isNgrok = tunnelConfig.provider === 'ngrok';

    if (isLoading) return <Card><CardContent className="p-6"><Skeleton className="h-96 w-full" /></CardContent></Card>;

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-end">
                <Button size="sm" onClick={handleSave} disabled={updateMutation.isPending}>
                    <Save className="h-4 w-4 mr-2" />
                    {updateMutation.isPending ? 'Saving...' : 'Save All'}
                </Button>
            </div>

            {/* Tunnel Card */}
            <Card>
                <CardHeader className="pb-3">
                    <CardTitle className="text-sm flex items-center gap-2 capitalize">
                        <Globe className="h-4 w-4" />
                        Tunnel
                        {tunnelStatus?.running && (
                            <Badge variant="outline" className="text-emerald-400 border-emerald-400/30 bg-emerald-400/10 text-xs">Running</Badge>
                        )}
                        {!tunnelStatus?.running && (
                            <Badge variant="outline" className="text-xs">Stopped</Badge>
                        )}
                    </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                        <div>
                            <label className="text-xs text-muted-foreground font-mono">provider</label>
                            <select
                                className="w-full border rounded px-2 py-1.5 text-sm bg-background font-mono"
                                value={tunnelConfig.provider || ''}
                                onChange={(e) => {
                                    const provider = e.target.value;
                                    const next = {
                                        ...tunnelConfig,
                                        provider,
                                    };
                                    setEditValues((prev) => ({
                                        ...prev,
                                        tunnel: next,
                                    }));
                                    tunnelConfigMutation.mutate(next as TunnelConfig);
                                }}
                            >
                                <option value="">Disabled</option>
                                <option value="cloudflare">Cloudflare</option>
                                <option value="ngrok">ngrok</option>
                            </select>
                        </div>
                        <div>
                            <label className="text-xs text-muted-foreground font-mono">api_token</label>
                            <Input
                                type="password"
                                placeholder={isCloudflare ? 'Cloudflare API token' : isNgrok ? 'ngrok auth token' : 'API token'}
                                value={tunnelConfig.api_token || ''}
                                onChange={(e) => {
                                    const next = { ...tunnelConfig, api_token: e.target.value };
                                    setEditValues((prev) => ({ ...prev, tunnel: next }));
                                }}
                                onBlur={() => tunnelConfigMutation.mutate(tunnelConfig as TunnelConfig)}
                                className="font-mono text-sm"
                            />
                        </div>
                        <div>
                            <label className="text-xs text-muted-foreground font-mono">public_url</label>
                            <Input
                                type="text"
                                placeholder="Auto-populated when tunnel starts"
                                value={tunnelStatus?.public_url || tunnelConfig.public_url || ''}
                                readOnly
                                className="font-mono text-sm bg-muted"
                            />
                        </div>
                    </div>

                    {/* Cloudflare extra fields */}
                    {isCloudflare && (
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                            <div>
                                <label className="text-xs text-muted-foreground font-mono">account_id</label>
                                <Input
                                    type="text"
                                    placeholder="Cloudflare account ID"
                                    value={tunnelConfig.account_id || ''}
                                    onChange={(e) => {
                                        const next = { ...tunnelConfig, account_id: e.target.value };
                                        setEditValues((prev) => ({ ...prev, tunnel: next }));
                                    }}
                                    onBlur={() => tunnelConfigMutation.mutate(tunnelConfig as TunnelConfig)}
                                    className="font-mono text-sm"
                                />
                            </div>
                            <div>
                                <label className="text-xs text-muted-foreground font-mono">zone_id</label>
                                <Input
                                    type="text"
                                    placeholder="Cloudflare zone ID"
                                    value={tunnelConfig.zone_id || ''}
                                    onChange={(e) => {
                                        const next = { ...tunnelConfig, zone_id: e.target.value };
                                        setEditValues((prev) => ({ ...prev, tunnel: next }));
                                    }}
                                    onBlur={() => tunnelConfigMutation.mutate(tunnelConfig as TunnelConfig)}
                                    className="font-mono text-sm"
                                />
                            </div>
                            <div>
                                <label className="text-xs text-muted-foreground font-mono">hostname (optional)</label>
                                <Input
                                    type="text"
                                    placeholder="api.example.com"
                                    value={tunnelConfig.hostname || ''}
                                    onChange={(e) => {
                                        const next = { ...tunnelConfig, hostname: e.target.value };
                                        setEditValues((prev) => ({ ...prev, tunnel: next }));
                                    }}
                                    onBlur={() => tunnelConfigMutation.mutate(tunnelConfig as TunnelConfig)}
                                    className="font-mono text-sm"
                                />
                                <p className="text-[10px] text-muted-foreground mt-1">
                                    Your Cloudflare domain/subdomain. Leave empty to create tunnel without DNS config.
                                </p>
                            </div>
                        </div>
                    )}

                    {tunnelStatus?.error && (
                        <div className="text-xs text-red-400 whitespace-pre-wrap">{tunnelStatus.error}</div>
                    )}
                    {tunnelStatus?.running && tunnelStatus?.public_url && (
                        <div className="text-xs text-emerald-400">
                            Tunnel active: {tunnelStatus.public_url}
                        </div>
                    )}
                    <div className="flex items-center gap-2">
                        <Button
                            size="sm"
                            disabled={!tunnelConfig.provider || tunnelStatus?.running || startTunnelMutation.isPending}
                            onClick={() => startTunnelMutation.mutate()}
                        >
                            <Play className="h-4 w-4 mr-1" /> Start
                        </Button>
                        <Button
                            size="sm"
                            variant="secondary"
                            disabled={!tunnelStatus?.running || stopTunnelMutation.isPending}
                            onClick={() => stopTunnelMutation.mutate()}
                        >
                            <Square className="h-4 w-4 mr-1" /> Stop
                        </Button>
                    </div>
                </CardContent>
            </Card>

            {Object.entries(editValues).map(([category, values]) => {
                if (category === 'tunnel') return null;
                return (
                    <Card key={category}>
                        <CardHeader className="pb-3">
                            <CardTitle className="text-sm flex items-center gap-2 capitalize">
                                <Settings className="h-4 w-4" />
                                {category.replace(/_/g, ' ')}
                            </CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                {Object.entries(values).map(([key, value]) => (
                                    <div key={key}>
                                        <label className="text-xs text-muted-foreground font-mono">{key}</label>
                                        <Input
                                            type={category === 'search' && key === 'web_search_api_key' ? 'password' : 'text'}
                                            value={typeof value === 'string' ? value : JSON.stringify(value)}
                                            onChange={(e) => setEditValues((prev) => ({
                                                ...prev,
                                                [category]: { ...prev[category], [key]: e.target.value },
                                            }))}
                                            className="font-mono text-sm"
                                        />
                                    </div>
                                ))}
                            </div>
                        </CardContent>
                    </Card>
                );
            })}
        </div>
    );
}

'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'next/navigation';
import { usersApi, modelMappingsApi } from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import { Skeleton } from '@/components/ui/skeleton';
import { Separator } from '@/components/ui/separator';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { toast } from 'sonner';
import { useState, useEffect } from 'react';
import { Copy, Save, X, ArrowLeft } from 'lucide-react';
import Link from 'next/link';

function formatTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return value.toLocaleString();
}

function formatDuration(ms: number | null): string {
  if (ms === null || ms === undefined) return '-';
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${ms}ms`;
}

function StatusBadge({ statusCode }: { statusCode: number | null }) {
  if (statusCode === null || statusCode === undefined) {
    return <Badge variant="outline" className="text-muted-foreground">-</Badge>;
  }
  if (statusCode >= 200 && statusCode < 300) {
    return <Badge variant="outline" className="text-emerald-400 border-emerald-400/30 bg-emerald-400/10">{statusCode}</Badge>;
  }
  if (statusCode >= 400 && statusCode < 500) {
    return <Badge variant="outline" className="text-amber-400 border-amber-400/30 bg-amber-400/10">{statusCode}</Badge>;
  }
  if (statusCode >= 500) {
    return <Badge variant="outline" className="text-red-400 border-red-400/30 bg-red-400/10">{statusCode}</Badge>;
  }
  return <Badge variant="outline">{statusCode}</Badge>;
}

export default function UserDetailPage() {
    const params = useParams();
    const username = params.username as string;
    const queryClient = useQueryClient();

    const { data: user, isLoading } = useQuery({
        queryKey: ['users', username],
        queryFn: () => usersApi.get(username),
    });

    const { data: userModels } = useQuery({
        queryKey: ['users', username, 'models'],
        queryFn: () => usersApi.getModels(username),
    });

    const { data: allMappings } = useQuery({
        queryKey: ['model-mappings'],
        queryFn: modelMappingsApi.list,
    });

    const { data: limits } = useQuery({
        queryKey: ['users', username, 'limits'],
        queryFn: () => usersApi.getLimits(username),
    });

    const { data: activity } = useQuery({
        queryKey: ['users', username, 'activity'],
        queryFn: async () => {
            const res = await usersApi.getActivity(username);
            return (res as any)?.activities ?? res;
        },
    });

    const { data: tokenUsage } = useQuery({
        queryKey: ['users', username, 'token-usage'],
        queryFn: () => usersApi.getTokenUsage(username),
    });

    const { data: modelUsage } = useQuery({
        queryKey: ['users', username, 'model-usage'],
        queryFn: () => usersApi.getModelUsage(username),
    });

    const [reqLimit, setReqLimit] = useState('');
    const [tokenLimit, setTokenLimit] = useState('');
    const [hasAllModels, setHasAllModels] = useState(false);
    const [selectedModels, setSelectedModels] = useState<string[]>([]);

    useEffect(() => {
        if (limits) {
            // eslint-disable-next-line react-hooks/set-state-in-effect
            setReqLimit(limits.request_limit?.toString() || '');
            // eslint-disable-next-line react-hooks/set-state-in-effect
            setTokenLimit(limits.token_limit?.toString() || '');
        }
    }, [limits]);

    useEffect(() => {
        if (userModels) {
            // eslint-disable-next-line react-hooks/set-state-in-effect
            setHasAllModels(userModels.has_all_models);
            // eslint-disable-next-line react-hooks/set-state-in-effect
            setSelectedModels(userModels.models);
        }
    }, [userModels]);

    const modelsMutation = useMutation({
        mutationFn: () =>
            hasAllModels
                ? usersApi.setAllModels(username)
                : usersApi.setModels(username, selectedModels),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['users', username, 'models'] });
            toast.success('Model access updated');
        },
        onError: (err) => toast.error(err.message),
    });

    const limitsMutation = useMutation({
        mutationFn: () =>
            usersApi.setLimits(username, {
                request_limit: reqLimit ? parseInt(reqLimit) : undefined,
                token_limit: tokenLimit ? parseInt(tokenLimit) : undefined,
            }),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['users', username, 'limits'] });
            toast.success('Limits updated');
        },
        onError: (err) => toast.error(err.message),
    });

    const removeLimitsMutation = useMutation({
        mutationFn: () => usersApi.removeLimits(username),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['users', username, 'limits'] });
            setReqLimit('');
            setTokenLimit('');
            toast.success('Limits removed');
        },
        onError: (err) => toast.error(err.message),
    });

    if (isLoading || !user) {
        return <Skeleton className="h-96 w-full" />;
    }

    const toggleModel = (model: string) => {
        setSelectedModels((prev) =>
            prev.includes(model) ? prev.filter((m) => m !== model) : [...prev, model]
        );
    };

    // Type-safe access to activity data
    type ActivityItem = {
        id: number;
        model_name: string;
        request_type: string;
        prompt_tokens: number;
        completion_tokens: number;
        total_tokens: number;
        status_code: number | null;
        duration_ms: number | null;
        error_message: string | null;
        created_at: string;
    };
    const activities: ActivityItem[] = (activity as any[]) || [];

    // Type-safe access to token usage data
    // Backend returns { username, usage: { prompt_tokens, completion_tokens, total_tokens, total_requests }, period }
    const usage = (tokenUsage as any)?.usage;

    // Type-safe access to model usage data
    // Backend returns { username, model_usage: [...], period }
    const modelUsageData = (modelUsage as any)?.model_usage;

    return (
        <div className="space-y-6">
            <div className="flex items-center gap-3">
                <Link href="/users">
                    <Button variant="ghost" size="icon"><ArrowLeft className="h-4 w-4" /></Button>
                </Link>
                <div>
                    <h2 className="text-xl font-bold">{user.username}</h2>
                    <p className="text-sm text-muted-foreground">
                        Created: {new Date(user.created_at).toLocaleDateString('en-US')}
                    </p>
                </div>
                <Badge className="ml-2" variant={user.is_active ? 'default' : 'secondary'}>
                    {user.is_active ? 'Active' : 'Inactive'}
                </Badge>
            </div>

            <Card>
                <CardHeader className="pb-3">
                    <CardTitle className="text-sm">API Token</CardTitle>
                </CardHeader>
                <CardContent>
                    <div className="flex items-center gap-2">
                        <code className="flex-1 text-xs bg-muted px-3 py-2 rounded font-mono break-all">
                            {user.token}
                        </code>
                        <Button
                            variant="outline" size="sm"
                            onClick={() => { navigator.clipboard.writeText(user.token); toast.success('Copied'); }}
                        >
                            <Copy className="h-4 w-4 mr-1" /> Copy
                        </Button>
                    </div>
                </CardContent>
            </Card>

            <Tabs defaultValue="models">
                <TabsList>
                    <TabsTrigger value="models">Models</TabsTrigger>
                    <TabsTrigger value="limits">Limits</TabsTrigger>
                    <TabsTrigger value="activity">Activity</TabsTrigger>
                </TabsList>

                <TabsContent value="models" className="mt-4">
                    <Card>
                        <CardHeader className="pb-3">
                            <div className="flex items-center justify-between">
                                <CardTitle className="text-sm">Model Access</CardTitle>
                                <Button size="sm" onClick={() => modelsMutation.mutate()} disabled={modelsMutation.isPending}>
                                    <Save className="h-4 w-4 mr-1" /> Save
                                </Button>
                            </div>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="flex items-center gap-3">
                                <Switch checked={hasAllModels} onCheckedChange={setHasAllModels} />
                                <span className="text-sm">Access to all models</span>
                            </div>
                            {!hasAllModels && allMappings && (
                                <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
                                    {allMappings.map((m) => (
                                        <label key={m.display_name} className="flex items-center gap-2 p-2 rounded-lg hover:bg-accent cursor-pointer">
                                            <input
                                                type="checkbox"
                                                checked={selectedModels.includes(m.display_name)}
                                                onChange={() => toggleModel(m.display_name)}
                                                className="rounded border-border"
                                            />
                                            <span className="text-sm">{m.display_name}</span>
                                        </label>
                                    ))}
                                </div>
                            )}
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="limits" className="mt-4">
                    <Card>
                        <CardHeader className="pb-3">
                            <div className="flex items-center justify-between">
                                <CardTitle className="text-sm">Usage Limits</CardTitle>
                                <div className="flex gap-2">
                                    <Button variant="ghost" size="sm" onClick={() => removeLimitsMutation.mutate()}>
                                        <X className="h-4 w-4 mr-1" /> Remove Limits
                                    </Button>
                                    <Button size="sm" onClick={() => limitsMutation.mutate()} disabled={limitsMutation.isPending}>
                                        <Save className="h-4 w-4 mr-1" /> Save
                                    </Button>
                                </div>
                            </div>
                        </CardHeader>
                        <CardContent>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <div>
                                    <label className="text-sm text-muted-foreground">Request Limit</label>
                                    <Input
                                        type="number" placeholder="Unlimited"
                                        value={reqLimit} onChange={(e) => setReqLimit(e.target.value)}
                                    />
                                </div>
                                <div>
                                    <label className="text-sm text-muted-foreground">Token Limit</label>
                                    <Input
                                        type="number" placeholder="Unlimited"
                                        value={tokenLimit} onChange={(e) => setTokenLimit(e.target.value)}
                                    />
                                </div>
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="activity" className="mt-4">
                    {/* Token Usage Summary */}
                    {usage && (
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
                            <Card>
                                <CardContent className="p-4">
                                    <p className="text-xs text-muted-foreground">Total Requests</p>
                                    <p className="text-lg font-bold">{(usage.total_requests || 0).toLocaleString()}</p>
                                </CardContent>
                            </Card>
                            <Card>
                                <CardContent className="p-4">
                                    <p className="text-xs text-muted-foreground">Prompt Tokens</p>
                                    <p className="text-lg font-bold">{formatTokens(usage.prompt_tokens || 0)}</p>
                                </CardContent>
                            </Card>
                            <Card>
                                <CardContent className="p-4">
                                    <p className="text-xs text-muted-foreground">Completion Tokens</p>
                                    <p className="text-lg font-bold">{formatTokens(usage.completion_tokens || 0)}</p>
                                </CardContent>
                            </Card>
                            <Card>
                                <CardContent className="p-4">
                                    <p className="text-xs text-muted-foreground">Total Tokens</p>
                                    <p className="text-lg font-bold">{formatTokens(usage.total_tokens || 0)}</p>
                                </CardContent>
                            </Card>
                        </div>
                    )}

                    {/* Model Usage Breakdown */}
                    {modelUsageData && Array.isArray(modelUsageData) && modelUsageData.length > 0 && (
                        <Card className="mb-4">
                            <CardHeader className="pb-2">
                                <CardTitle className="text-sm">Model Usage</CardTitle>
                            </CardHeader>
                            <CardContent className="p-0 overflow-x-auto">
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Model</TableHead>
                                            <TableHead className="text-right">Requests</TableHead>
                                            <TableHead className="text-right">Total Tokens</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {modelUsageData.map((m: any, i: number) => (
                                            <TableRow key={i}>
                                                <TableCell className="font-mono text-xs">{m.model_name}</TableCell>
                                                <TableCell className="text-right text-xs">{(m.request_count || 0).toLocaleString()}</TableCell>
                                                <TableCell className="text-right text-xs font-medium">{formatTokens(m.total_tokens || 0)}</TableCell>
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            </CardContent>
                        </Card>
                    )}

                    {/* Activity Log Table */}
                    <Card>
                        <CardHeader className="pb-2">
                            <CardTitle className="text-sm">Request Log</CardTitle>
                        </CardHeader>
                        <CardContent className="p-0 overflow-x-auto">
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>Model</TableHead>
                                        <TableHead>Type</TableHead>
                                        <TableHead className="text-right">Prompt</TableHead>
                                        <TableHead className="text-right">Completion</TableHead>
                                        <TableHead className="text-right">Total</TableHead>
                                        <TableHead>Status</TableHead>
                                        <TableHead className="text-right">Duration</TableHead>
                                        <TableHead>Time</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {activities.map((log) => (
                                        <TableRow key={log.id}>
                                            <TableCell className="font-mono text-xs">{log.model_name}</TableCell>
                                            <TableCell className="text-xs">{log.request_type}</TableCell>
                                            <TableCell className="text-right text-xs">{(log.prompt_tokens ?? 0).toLocaleString()}</TableCell>
                                            <TableCell className="text-right text-xs">{(log.completion_tokens ?? 0).toLocaleString()}</TableCell>
                                            <TableCell className="text-right text-xs font-medium">{(log.total_tokens ?? 0).toLocaleString()}</TableCell>
                                            <TableCell><StatusBadge statusCode={log.status_code} /></TableCell>
                                            <TableCell className="text-right text-xs">{formatDuration(log.duration_ms)}</TableCell>
                                            <TableCell className="text-xs text-muted-foreground whitespace-nowrap">
                                                {log.created_at ? new Date(log.created_at).toLocaleString('en-US') : '-'}
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                    {activities.length === 0 && (
                                        <TableRow>
                                            <TableCell colSpan={8} className="text-center text-muted-foreground py-12">
                                                No activity yet
                                            </TableCell>
                                        </TableRow>
                                    )}
                                </TableBody>
                            </Table>
                        </CardContent>
                    </Card>
                </TabsContent>
            </Tabs>
        </div>
    );
}
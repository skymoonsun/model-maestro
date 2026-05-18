'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useParams, useSearchParams } from 'next/navigation';
import { nodesApi } from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Progress } from '@/components/ui/progress';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table';
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogFooter,
    DialogTrigger,
} from '@/components/ui/dialog';
import { toast } from 'sonner';
import { useState, useCallback, useEffect } from 'react';
import {
    ArrowLeft,
    Server,
    Heart,
    RefreshCw,
    Download,
    Activity,
    HardDrive,
    Key,
    Shield,
    ExternalLink,
} from 'lucide-react';
import Link from 'next/link';

function formatSize(bytes: number) {
    if (bytes >= 1_000_000_000) return `${(bytes / 1_000_000_000).toFixed(1)} GB`;
    if (bytes >= 1_000_000) return `${(bytes / 1_000_000).toFixed(0)} MB`;
    return `${(bytes / 1_000).toFixed(0)} KB`;
}

function HealthBadge({ status }: { status: string }) {
    const map: Record<string, string> = {
        healthy: 'Healthy',
        unhealthy: 'Unhealthy',
        unknown: 'Unknown',
    };
    return (
        <Badge
            variant={status === 'healthy' ? 'default' : status === 'unhealthy' ? 'destructive' : 'secondary'}
            className={
                status === 'healthy'
                    ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
                    : status === 'unhealthy'
                    ? 'bg-destructive/20 text-destructive border-destructive/30'
                    : ''
            }
        >
            {map[status] ?? status}
        </Badge>
    );
}

export default function NodeDetailPage() {
    const params = useParams();
    const nodeId = Number(params.id);
    const qc = useQueryClient();
    const [healthChecking, setHealthChecking] = useState(false);
    const [syncing, setSyncing] = useState(false);
    const [pullModel, setPullModel] = useState('');
    const [pulling, setPulling] = useState(false);
    const [progress, setProgress] = useState(0);
    const [status, setStatus] = useState('');
    const [googleAuthing, setGoogleAuthing] = useState(false);
    const [googleRefreshing, setGoogleRefreshing] = useState(false);

    // Handle OAuth redirect query params
    const searchParams = useSearchParams();
    useEffect(() => {
        const oauth = searchParams.get('oauth');
        const msg = searchParams.get('msg');
        if (oauth === 'success') {
            toast.success('Google OAuth connected successfully');
            qc.invalidateQueries({ queryKey: ['nodes', nodeId] });
            // Clean up URL
            window.history.replaceState({}, '', `/nodes/${nodeId}`);
        } else if (oauth === 'error') {
            toast.error(msg ? decodeURIComponent(msg) : 'Google OAuth failed');
            window.history.replaceState({}, '', `/nodes/${nodeId}`);
        }
    }, [searchParams, nodeId, qc]);

    const { data: node, isLoading } = useQuery({
        queryKey: ['nodes', nodeId],
        queryFn: () => nodesApi.get(nodeId),
        enabled: !isNaN(nodeId),
    });

    const { data: metrics } = useQuery({
        queryKey: ['nodes', nodeId, 'metrics'],
        queryFn: () => nodesApi.getMetrics(nodeId),
        enabled: !isNaN(nodeId) && !!node,
    });

    const handleHealthCheck = async () => {
        setHealthChecking(true);
        try {
            const r = await nodesApi.healthCheck(nodeId);
            toast.success(`${r.node_name}: ${r.health_status}`);
            qc.invalidateQueries({ queryKey: ['nodes', nodeId] });
        } catch (e) {
            toast.error(e instanceof Error ? e.message : 'Health check failed');
        } finally {
            setHealthChecking(false);
        }
    };

    const handleSync = async () => {
        setSyncing(true);
        try {
            const r = await nodesApi.syncModels(nodeId);
            toast.success(`${r.node_name}: ${r.synced_count} models synced`);
            qc.invalidateQueries({ queryKey: ['nodes', nodeId] });
        } catch (e) {
            toast.error(e instanceof Error ? e.message : 'Sync failed');
        } finally {
            setSyncing(false);
        }
    };

    const handlePull = useCallback(async () => {
        if (!pullModel.trim()) return;
        setPulling(true);
        setProgress(0);
        setStatus('Starting...');
        try {
            await nodesApi.pullModel(nodeId, pullModel.trim(), (data) => {
                if (data.status) setStatus(data.status as string);
                if (data.total && data.completed) {
                    setProgress(((data.completed as number) / (data.total as number)) * 100);
                }
                if (data.status === 'success') {
                    setProgress(100);
                }
                if ((data as { node?: string }).node) {
                    setStatus(`${(data as { node: string }).node}: ${data.status}`);
                }
            });
            toast.success(`${pullModel} pulled to ${node?.name}`);
            qc.invalidateQueries({ queryKey: ['nodes', nodeId] });
            setPullModel('');
        } catch (err: unknown) {
            toast.error(`Pull error: ${err instanceof Error ? err.message : 'Unknown error'}`);
        } finally {
            setPulling(false);
            setProgress(0);
            setStatus('');
        }
    }, [nodeId, pullModel, node?.name, qc]);

    if (isLoading || !node) {
        return (
            <div className="space-y-4">
                <Skeleton className="h-8 w-48" />
                <Skeleton className="h-64 w-full" />
            </div>
        );
    }

    return (
        <div className="space-y-6">
            <div className="flex items-center gap-4">
                <Link href="/nodes">
                    <Button variant="ghost" size="icon">
                        <ArrowLeft className="h-4 w-4" />
                    </Button>
                </Link>
                <div className="flex-1">
                    <h1 className="text-2xl font-semibold flex items-center gap-2">
                        <Server className="h-6 w-6" />
                        {node.name}
                    </h1>
                    <p className="text-sm text-muted-foreground font-mono">
                        {node.node_type === 'antigravity' ? 'Google v1internal API' : node.base_url}
                    </p>
                </div>
                {node.node_type === 'antigravity' && (
                    <Badge
                        variant="outline"
                        className="bg-green-500/10 text-green-400 border-green-500/30"
                    >
                        Antigravity
                    </Badge>
                )}
                <HealthBadge status={node.health_status} />
                {!node.is_active && (
                    <Badge variant="outline" className="text-muted-foreground">
                        Inactive
                    </Badge>
                )}
            </div>

            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium flex items-center gap-2">
                            <HardDrive className="h-4 w-4" />
                            Models
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-2xl font-bold">{node.model_count}</p>
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium flex items-center gap-2">
                            <Activity className="h-4 w-4" />
                            Active Requests
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-2xl font-bold">{metrics?.active_requests ?? 0}</p>
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium">Requests Today</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-2xl font-bold">{metrics?.total_requests_today ?? 0}</p>
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium">Avg Response (ms)</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-2xl font-bold">
                            {metrics?.avg_response_time_ms != null
                                ? Math.round(metrics.avg_response_time_ms)
                                : '—'}
                        </p>
                    </CardContent>
                </Card>
            </div>

            {/* Antigravity OAuth Status Card */}
            {node.node_type === 'antigravity' && (
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium flex items-center gap-2">
                            <Shield className="h-4 w-4" />
                            Google OAuth Status
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        <div className="flex items-center gap-2">
                            <Key className="h-4 w-4 text-muted-foreground" />
                            <span className="text-sm">
                                Status: {' '}
                                {node.oauth_tokens?.access_token ? (
                                    <span className="text-green-400 font-medium">Connected</span>
                                ) : (
                                    <span className="text-amber-400 font-medium">Not Connected</span>
                                )}
                            </span>
                        </div>
                        {node.project_id && (
                            <div className="flex items-center gap-2">
                                <Server className="h-4 w-4 text-muted-foreground" />
                                <span className="text-sm text-muted-foreground">Project: {node.project_id}</span>
                            </div>
                        )}
                        {node.oauth_tokens?.obtained_at && (
                            <div className="text-xs text-muted-foreground">
                                Token obtained: {new Date(node.oauth_tokens.obtained_at).toLocaleString()}
                            </div>
                        )}
                        <div className="flex gap-2 pt-1">
                            <Button
                                variant="outline"
                                size="sm"
                                disabled={googleAuthing}
                                onClick={async () => {
                                    setGoogleAuthing(true);
                                    try {
                                        const r = await nodesApi.googleAuthUrl(nodeId);
                                        window.open(r.auth_url, '_blank');
                                    } catch (e) {
                                        toast.error(e instanceof Error ? e.message : 'Failed to get auth URL');
                                    } finally {
                                        setGoogleAuthing(false);
                                    }
                                }}
                            >
                                {googleAuthing ? (
                                    <RefreshCw className="h-4 w-4 animate-spin mr-2" />
                                ) : (
                                    <ExternalLink className="h-4 w-4 mr-2" />
                                )}
                                Google Auth
                            </Button>
                            {node.oauth_tokens?.access_token && (
                                <Button
                                    variant="outline"
                                    size="sm"
                                    disabled={googleRefreshing}
                                    onClick={async () => {
                                        setGoogleRefreshing(true);
                                        try {
                                            const r = await nodesApi.googleRefreshToken(nodeId);
                                            toast.success(`Token refreshed, expires in ${r.expires_in}s`);
                                            qc.invalidateQueries({ queryKey: ['nodes', nodeId] });
                                        } catch (e) {
                                            toast.error(e instanceof Error ? e.message : 'Refresh failed');
                                        } finally {
                                            setGoogleRefreshing(false);
                                        }
                                    }}
                                >
                                    {googleRefreshing ? (
                                        <RefreshCw className="h-4 w-4 animate-spin mr-2" />
                                    ) : (
                                        <RefreshCw className="h-4 w-4 mr-2" />
                                    )}
                                    Refresh Token
                                </Button>
                            )}
                        </div>
                    </CardContent>
                </Card>
            )}

            <div className="flex gap-2">
                <Button variant="outline" onClick={handleHealthCheck} disabled={healthChecking}>
                    {healthChecking ? (
                        <RefreshCw className="h-4 w-4 animate-spin mr-2" />
                    ) : (
                        <Heart className="h-4 w-4 mr-2" />
                    )}
                    Health Check
                </Button>
                <Button variant="outline" onClick={handleSync} disabled={syncing}>
                    {syncing ? (
                        <RefreshCw className="h-4 w-4 animate-spin mr-2" />
                    ) : (
                        <RefreshCw className="h-4 w-4 mr-2" />
                    )}
                    Sync Models
                </Button>
            </div>

            {node.node_type !== 'antigravity' && (
                <Card>
                    <CardHeader className="pb-3">
                        <CardTitle className="text-sm font-medium flex items-center gap-2">
                            <Download className="h-4 w-4" /> Pull Model to this Node
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        <div className="flex gap-2">
                            <Input
                                placeholder="Model name (e.g., llama3.3:70b)"
                                value={pullModel}
                                onChange={(e) => setPullModel(e.target.value)}
                                onKeyDown={(e) => e.key === 'Enter' && !pulling && handlePull()}
                                disabled={pulling}
                            />
                            <Button onClick={handlePull} disabled={!pullModel.trim() || pulling}>
                                {pulling ? 'Pulling...' : 'Pull'}
                            </Button>
                        </div>
                        {pulling && (
                            <div className="space-y-2">
                                <Progress value={progress} className="h-2" />
                                <p className="text-xs text-muted-foreground">{status} — %{progress.toFixed(0)}</p>
                            </div>
                        )}
                    </CardContent>
                </Card>
            )}

            <Card>
                <CardHeader>
                    <CardTitle className="text-sm font-medium">Models on this Node</CardTitle>
                </CardHeader>
                <CardContent className="p-0">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Model</TableHead>
                                <TableHead>Size</TableHead>
                                <TableHead>Family</TableHead>
                                <TableHead className="text-right">Available</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {node.models?.length === 0 ? (
                                <TableRow>
                                    <TableCell colSpan={4} className="text-center text-muted-foreground py-8">
                                        No models. Sync or pull models.
                                    </TableCell>
                                </TableRow>
                            ) : (
                                node.models?.map((m) => (
                                    <TableRow key={m.model_name} className={m.is_available ? '' : 'opacity-50 bg-muted/20'}>
                                        <TableCell className="font-mono text-sm">{m.model_name}</TableCell>
                                        <TableCell>{formatSize(m.model_size)}</TableCell>
                                        <TableCell className="text-muted-foreground">
                                            {m.model_family || '—'}
                                        </TableCell>
                                        <TableCell className="text-right">
                                            <Switch
                                                checked={m.is_available}
                                                onCheckedChange={(checked) => {
                                                    nodesApi.toggleModelAvailable(nodeId, m.model_name, checked)
                                                        .then((data) => {
                                                            toast.success(`${data.model_name} is now ${data.is_available ? 'active' : 'inactive'}`);
                                                            qc.invalidateQueries({ queryKey: ['nodes', nodeId] });
                                                        })
                                                        .catch((err: Error) => toast.error(err.message));
                                                }}
                                                aria-label="Toggle availability"
                                            />
                                        </TableCell>
                                    </TableRow>
                                ))
                            )}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>
        </div>
    );
}

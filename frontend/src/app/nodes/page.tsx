'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { nodesApi, type Node, type CreateNode } from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Skeleton } from '@/components/ui/skeleton';
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogFooter,
    DialogTrigger,
} from '@/components/ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table';
import { toast } from 'sonner';
import { useState, useEffect } from 'react';
import {
    Plus,
    RefreshCw,
    Heart,
    Server,
    Trash2,
    Pencil,
    Activity,
    BarChart3,
    Link as LinkIcon,
} from 'lucide-react';
import Link from 'next/link';

function HealthBadge({ status }: { status: string }) {
    const map: Record<string, { variant: 'default' | 'secondary' | 'destructive' | 'outline'; label: string }> = {
        healthy: {
            variant: 'default',
            label: 'Healthy',
        },
        unhealthy: {
            variant: 'destructive',
            label: 'Unhealthy',
        },
        unknown: {
            variant: 'secondary',
            label: 'Unknown',
        },
    };
    const { label } = map[status] ?? { label: status };
    return (
        <Badge
            variant={map[status]?.variant ?? 'outline'}
            className={
                status === 'healthy'
                    ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
                    : status === 'unhealthy'
                    ? 'bg-destructive/20 text-destructive border-destructive/30'
                    : ''
            }
        >
            {label}
        </Badge>
    );
}

function NodeCard({
    node,
    onHealthCheck,
    onSync,
    onDelete,
    healthChecking,
    syncing,
}: {
    node: Node;
    onHealthCheck: (id: number) => void;
    onSync: (id: number) => void;
    onDelete: (id: number) => void;
    healthChecking: number | null;
    syncing: number | null;
}) {
    const qc = useQueryClient();
    const [editOpen, setEditOpen] = useState(false);
    const [form, setForm] = useState({
        name: node.name,
        base_url: node.base_url,
        priority: node.priority,
        weight: node.weight,
        is_active: node.is_active,
    });

    const updateMut = useMutation({
        mutationFn: (data: Partial<CreateNode>) => nodesApi.update(node.id, data),
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['nodes'] });
            setEditOpen(false);
            toast.success('Node updated');
        },
        onError: (e) => toast.error(e.message),
    });

    useEffect(() => {
        if (editOpen) {
            setForm({
                name: node.name,
                base_url: node.base_url,
                priority: node.priority,
                weight: node.weight,
                is_active: node.is_active,
            });
        }
    }, [editOpen, node.name, node.base_url, node.priority, node.weight, node.is_active]);

    return (
        <Card className="overflow-hidden">
            <CardHeader className="pb-2">
                <div className="flex items-start justify-between">
                    <div className="flex items-center gap-2">
                        <Server className="h-5 w-5 text-muted-foreground" />
                        <CardTitle className="text-base">{node.name}</CardTitle>
                        {!node.is_active && (
                            <Badge variant="outline" className="text-muted-foreground">
                                Inactive
                            </Badge>
                        )}
                    </div>
                    <HealthBadge status={node.health_status} />
                </div>
                <p className="text-xs text-muted-foreground font-mono mt-1 truncate" title={node.base_url}>
                    {node.base_url}
                </p>
            </CardHeader>
            <CardContent className="space-y-4">
                <div className="flex items-center justify-between text-sm">
                    <span className="text-muted-foreground">Models</span>
                    <Badge variant="outline">{node.model_count}</Badge>
                </div>
                <div className="flex flex-wrap gap-2">
                    <Button
                        size="sm"
                        variant="outline"
                        onClick={() => onHealthCheck(node.id)}
                        disabled={healthChecking !== null}
                    >
                        {healthChecking === node.id ? (
                            <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                            <Heart className="h-3.5 w-3.5" />
                        )}
                        <span className="ml-1">Health</span>
                    </Button>
                    <Button
                        size="sm"
                        variant="outline"
                        onClick={() => onSync(node.id)}
                        disabled={syncing !== null}
                    >
                        {syncing === node.id ? (
                            <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                            <RefreshCw className="h-3.5 w-3.5" />
                        )}
                        <span className="ml-1">Sync</span>
                    </Button>
                    <Dialog open={editOpen} onOpenChange={setEditOpen}>
                        <DialogTrigger asChild>
                            <Button size="sm" variant="outline">
                                <Pencil className="h-3.5 w-3.5" />
                                <span className="ml-1">Edit</span>
                            </Button>
                        </DialogTrigger>
                        <DialogContent>
                            <DialogHeader>
                                <DialogTitle>Edit Node</DialogTitle>
                            </DialogHeader>
                            <div className="space-y-4 py-4">
                                <div>
                                    <Label>Name</Label>
                                    <Input
                                        value={form.name}
                                        onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                                    />
                                </div>
                                <div>
                                    <Label>Base URL</Label>
                                    <Input
                                        value={form.base_url}
                                        onChange={(e) => setForm((f) => ({ ...f, base_url: e.target.value }))}
                                    />
                                </div>
                                <div className="grid grid-cols-2 gap-4">
                                    <div>
                                        <Label>Priority</Label>
                                        <Input
                                            type="number"
                                            value={form.priority}
                                            onChange={(e) =>
                                                setForm((f) => ({ ...f, priority: Number(e.target.value) || 0 }))
                                            }
                                        />
                                    </div>
                                    <div>
                                        <Label>Weight</Label>
                                        <Input
                                            type="number"
                                            value={form.weight}
                                            onChange={(e) =>
                                                setForm((f) => ({ ...f, weight: Number(e.target.value) || 100 }))
                                            }
                                        />
                                    </div>
                                </div>
                                <div className="flex items-center gap-2">
                                    <Switch
                                        checked={form.is_active}
                                        onCheckedChange={(v) => setForm((f) => ({ ...f, is_active: v }))}
                                    />
                                    <Label>Active</Label>
                                </div>
                            </div>
                            <DialogFooter>
                                <Button variant="ghost" onClick={() => setEditOpen(false)}>
                                    Cancel
                                </Button>
                                <Button onClick={() => updateMut.mutate(form)} disabled={updateMut.isPending}>
                                    Save
                                </Button>
                            </DialogFooter>
                        </DialogContent>
                    </Dialog>
                    <Link href={`/nodes/${node.id}`}>
                        <Button size="sm" variant="outline">
                            <Activity className="h-3.5 w-3.5" />
                            <span className="ml-1">Details</span>
                        </Button>
                    </Link>
                    <Dialog>
                        <DialogTrigger asChild>
                            <Button size="sm" variant="ghost" className="text-destructive hover:text-destructive">
                                <Trash2 className="h-3.5 w-3.5" />
                            </Button>
                        </DialogTrigger>
                        <DialogContent>
                            <DialogHeader>
                                <DialogTitle>Delete Node</DialogTitle>
                            </DialogHeader>
                            <p className="text-sm text-muted-foreground py-4">
                                Are you sure you want to delete node <strong>{node.name}</strong>? This will remove all
                                model associations.
                            </p>
                            <DialogFooter>
                                <Button variant="ghost">Cancel</Button>
                                <Button variant="destructive" onClick={() => onDelete(node.id)}>
                                    Delete
                                </Button>
                            </DialogFooter>
                        </DialogContent>
                    </Dialog>
                </div>
            </CardContent>
        </Card>
    );
}

function AddNodeDialog({ onSuccess }: { onSuccess: () => void }) {
    const [open, setOpen] = useState(false);
    const [form, setForm] = useState<CreateNode>({
        name: '',
        base_url: 'http://localhost:11434',
        priority: 0,
        weight: 100,
        is_active: true,
    });
    const qc = useQueryClient();
    const mut = useMutation({
        mutationFn: nodesApi.create,
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['nodes'] });
            setOpen(false);
            setForm({ name: '', base_url: 'http://localhost:11434', priority: 0, weight: 100, is_active: true });
            toast.success('Node created');
            onSuccess();
        },
        onError: (e) => toast.error(e.message),
    });

    return (
        <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
                <Button>
                    <Plus className="h-4 w-4 mr-2" />
                    Add Node
                </Button>
            </DialogTrigger>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>Add Ollama Node</DialogTitle>
                </DialogHeader>
                <div className="space-y-4 py-4">
                    <div>
                        <Label>Name</Label>
                        <Input
                            placeholder="main-server"
                            value={form.name}
                            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                        />
                    </div>
                    <div>
                        <Label>Base URL</Label>
                        <Input
                            placeholder="http://192.168.1.10:11434"
                            value={form.base_url}
                            onChange={(e) => setForm((f) => ({ ...f, base_url: e.target.value }))}
                        />
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                        <div>
                            <Label>Priority (higher = preferred)</Label>
                            <Input
                                type="number"
                                value={form.priority}
                                onChange={(e) =>
                                    setForm((f) => ({ ...f, priority: Number(e.target.value) || 0 }))
                                }
                            />
                        </div>
                        <div>
                            <Label>Weight (load balancing)</Label>
                            <Input
                                type="number"
                                value={form.weight}
                                onChange={(e) =>
                                    setForm((f) => ({ ...f, weight: Number(e.target.value) || 100 }))
                                }
                            />
                        </div>
                    </div>
                    <div className="flex items-center gap-2">
                        <Switch
                            checked={form.is_active}
                            onCheckedChange={(v) => setForm((f) => ({ ...f, is_active: v }))}
                        />
                        <Label>Active</Label>
                    </div>
                </div>
                <DialogFooter>
                    <Button variant="ghost" onClick={() => setOpen(false)}>
                        Cancel
                    </Button>
                    <Button onClick={() => mut.mutate(form)} disabled={!form.name || !form.base_url || mut.isPending}>
                        Create
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

export default function NodesPage() {
    const qc = useQueryClient();
    const [healthChecking, setHealthChecking] = useState<number | null>(null);
    const [syncing, setSyncing] = useState<number | null>(null);

    const { data: nodes, isLoading } = useQuery({
        queryKey: ['nodes'],
        queryFn: () => nodesApi.list(),
    });

    const { data: distribution } = useQuery({
        queryKey: ['nodes-distribution'],
        queryFn: nodesApi.getDistribution,
    });

    const { data: loadStatus } = useQuery({
        queryKey: ['nodes-load-status'],
        queryFn: nodesApi.getLoadStatus,
    });

    const syncAllMut = useMutation({
        mutationFn: nodesApi.syncAll,
        onSuccess: (results) => {
            qc.invalidateQueries({ queryKey: ['nodes'] });
            qc.invalidateQueries({ queryKey: ['nodes-distribution'] });
            const total = results.reduce((a, r) => a + r.synced_count, 0);
            toast.success(`Synced ${results.length} nodes, ${total} models total`);
        },
        onError: (e) => toast.error(e.message),
    });

    const deleteMut = useMutation({
        mutationFn: nodesApi.delete,
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['nodes'] });
            qc.invalidateQueries({ queryKey: ['nodes-distribution'] });
            toast.success('Node deleted');
        },
        onError: (e) => toast.error(e.message),
    });

    const handleHealthCheck = async (id: number) => {
        setHealthChecking(id);
        try {
            const r = await nodesApi.healthCheck(id);
            toast.success(`${r.node_name}: ${r.health_status}`);
            qc.invalidateQueries({ queryKey: ['nodes'] });
        } catch (e) {
            toast.error(e instanceof Error ? e.message : 'Health check failed');
        } finally {
            setHealthChecking(null);
        }
    };

    const handleSync = async (id: number) => {
        setSyncing(id);
        try {
            const r = await nodesApi.syncModels(id);
            toast.success(`${r.node_name}: ${r.synced_count} models synced`);
            qc.invalidateQueries({ queryKey: ['nodes'] });
            qc.invalidateQueries({ queryKey: ['nodes-distribution'] });
        } catch (e) {
            toast.error(e instanceof Error ? e.message : 'Sync failed');
        } finally {
            setSyncing(null);
        }
    };

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <h1 className="text-2xl font-semibold">Load Balancing & Nodes</h1>
                <div className="flex gap-2">
                    <AddNodeDialog onSuccess={() => {}} />
                    <Button
                        variant="outline"
                        onClick={() => syncAllMut.mutate()}
                        disabled={syncAllMut.isPending || !nodes?.length}
                    >
                        {syncAllMut.isPending ? (
                            <RefreshCw className="h-4 w-4 animate-spin" />
                        ) : (
                            <RefreshCw className="h-4 w-4" />
                        )}
                        <span className="ml-2">Sync All</span>
                    </Button>
                </div>
            </div>

            <Tabs defaultValue="nodes">
                <TabsList>
                    <TabsTrigger value="nodes">
                        <Server className="h-4 w-4 mr-2" />
                        Nodes ({nodes?.length ?? 0})
                    </TabsTrigger>
                    <TabsTrigger value="distribution">
                        <BarChart3 className="h-4 w-4 mr-2" />
                        Model Distribution
                    </TabsTrigger>
                    <TabsTrigger value="load">
                        <Activity className="h-4 w-4 mr-2" />
                        Load Status
                    </TabsTrigger>
                </TabsList>

                <TabsContent value="nodes" className="mt-4">
                    {isLoading ? (
                        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                            {[1, 2, 3].map((i) => (
                                <Skeleton key={i} className="h-48" />
                            ))}
                        </div>
                    ) : nodes?.length === 0 ? (
                        <Card>
                            <CardContent className="flex flex-col items-center justify-center py-16">
                                <Server className="h-12 w-12 text-muted-foreground mb-4" />
                                <p className="text-muted-foreground mb-2">No nodes configured</p>
                                <p className="text-sm text-muted-foreground mb-4">
                                    Add your first Ollama node to enable load balancing
                                </p>
                                <AddNodeDialog onSuccess={() => {}} />
                            </CardContent>
                        </Card>
                    ) : (
                        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                            {nodes?.map((node) => (
                                <NodeCard
                                    key={node.id}
                                    node={node}
                                    onHealthCheck={handleHealthCheck}
                                    onSync={handleSync}
                                    onDelete={(id) => deleteMut.mutate(id)}
                                    healthChecking={healthChecking}
                                    syncing={syncing}
                                />
                            ))}
                        </div>
                    )}
                </TabsContent>

                <TabsContent value="distribution" className="mt-4">
                    <Card>
                        <CardHeader>
                            <CardTitle className="text-sm font-medium">Model Distribution</CardTitle>
                            <p className="text-xs text-muted-foreground">
                                Which models are available on which nodes
                            </p>
                        </CardHeader>
                        <CardContent>
                            {!distribution?.length ? (
                                <p className="text-sm text-muted-foreground py-8 text-center">
                                    No model distribution data. Sync nodes first.
                                </p>
                            ) : (
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Model</TableHead>
                                            <TableHead>Node Count</TableHead>
                                            <TableHead>Nodes</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {distribution.map((d) => (
                                            <TableRow key={d.model_name}>
                                                <TableCell className="font-mono text-sm">{d.model_name}</TableCell>
                                                <TableCell>
                                                    <Badge
                                                        variant="outline"
                                                        className={
                                                            d.node_count >= 2
                                                                ? 'border-emerald-500/50 text-emerald-400'
                                                                : d.node_count === 1
                                                                ? 'border-amber-500/50 text-amber-400'
                                                                : ''
                                                        }
                                                    >
                                                        {d.node_count}
                                                    </Badge>
                                                </TableCell>
                                                <TableCell className="text-sm text-muted-foreground">
                                                    {d.nodes.join(', ')}
                                                </TableCell>
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            )}
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="load" className="mt-4">
                    <Card>
                        <CardHeader>
                            <CardTitle className="text-sm font-medium">Load Balancer Status</CardTitle>
                            <p className="text-xs text-muted-foreground">Current node status and metrics</p>
                        </CardHeader>
                        <CardContent>
                            {!loadStatus?.length ? (
                                <p className="text-sm text-muted-foreground py-8 text-center">
                                    No nodes or load data available
                                </p>
                            ) : (
                                <div className="space-y-4">
                                    {loadStatus.map((s) => (
                                        <div
                                            key={s.id}
                                            className="flex items-center justify-between rounded-lg border p-4"
                                        >
                                            <div className="flex items-center gap-4">
                                                <LinkIcon className="h-5 w-5 text-muted-foreground" />
                                                <div>
                                                    <p className="font-medium">{s.name}</p>
                                                    <p className="text-xs text-muted-foreground font-mono">
                                                        {s.base_url}
                                                    </p>
                                                </div>
                                                <HealthBadge status={s.health_status} />
                                                {!s.is_active && (
                                                    <Badge variant="outline">Inactive</Badge>
                                                )}
                                            </div>
                                            <Badge variant="secondary">{s.model_count} models</Badge>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </CardContent>
                    </Card>
                </TabsContent>
            </Tabs>
        </div>
    );
}

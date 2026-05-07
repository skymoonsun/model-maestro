'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ollamaModelsApi, nodesApi, type OllamaModel } from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { Skeleton } from '@/components/ui/skeleton';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select';
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import {
    Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter, DialogClose,
} from '@/components/ui/dialog';
import { toast } from 'sonner';
import { useState, useCallback } from 'react';
import { Download, Trash2, Search, HardDrive, Server, RefreshCw } from 'lucide-react';
import Link from 'next/link';

function formatSize(bytes: number) {
    if (bytes >= 1_000_000_000) return `${(bytes / 1_000_000_000).toFixed(1)} GB`;
    if (bytes >= 1_000_000) return `${(bytes / 1_000_000).toFixed(0)} MB`;
    return `${(bytes / 1_000).toFixed(0)} KB`;
}

const PULL_ALL = 'all';

function PullModelSection() {
    const [modelName, setModelName] = useState('');
    const [pulling, setPulling] = useState(false);
    const [progress, setProgress] = useState(0);
    const [status, setStatus] = useState('');
    const [targetNode, setTargetNode] = useState<string>(PULL_ALL);
    const queryClient = useQueryClient();

    const { data: allNodes } = useQuery({
        queryKey: ['nodes'],
        queryFn: () => nodesApi.list(),
    });
    const nodes = allNodes?.filter((n) => n.node_type === 'ollama');

    const handlePull = useCallback(async () => {
        if (!modelName.trim()) return;
        setPulling(true);
        setProgress(0);
        setStatus('Starting...');

        try {
            if (nodes?.length && targetNode !== PULL_ALL) {
                const nodeId = Number(targetNode);
                await nodesApi.pullModel(nodeId, modelName.trim(), (data) => {
                    if (data.status) setStatus(data.status as string);
                    if (data.total && data.completed) {
                        setProgress(((data.completed as number) / (data.total as number)) * 100);
                    }
                    if (data.status === 'success') setProgress(100);
                    if ((data as { node?: string }).node) {
                        setStatus(`${(data as { node: string }).node}: ${data.status}`);
                    }
                });
            } else if (nodes?.length && targetNode === PULL_ALL) {
                await nodesApi.pullModelAll(modelName.trim(), (data) => {
                    if (data.status) setStatus(data.status as string);
                    if (data.total && data.completed) {
                        setProgress(((data.completed as number) / (data.total as number)) * 100);
                    }
                    if (data.status === 'success') setProgress(100);
                    if ((data as { node?: string }).node) {
                        setStatus(`${(data as { node: string }).node}: ${data.status}`);
                    }
                });
            } else {
                await ollamaModelsApi.pull(modelName.trim(), (data) => {
                    if (data.status) setStatus(data.status as string);
                    if (data.total && data.completed) {
                        setProgress(((data.completed as number) / (data.total as number)) * 100);
                    }
                    if (data.status === 'success') setProgress(100);
                });
            }
            toast.success(`${modelName} downloaded successfully`);
            queryClient.invalidateQueries({ queryKey: ['ollama-models'] });
            queryClient.invalidateQueries({ queryKey: ['nodes'] });
            setModelName('');
        } catch (err: unknown) {
            toast.error(`Pull error: ${err instanceof Error ? err.message : 'Unknown error'}`);
        } finally {
            setPulling(false);
            setProgress(0);
            setStatus('');
        }
    }, [modelName, targetNode, nodes?.length, queryClient]);

    return (
        <Card>
            <CardHeader className="pb-3">
                <CardTitle className="text-sm font-medium flex items-center gap-2">
                    <Download className="h-4 w-4" /> Model Pull
                </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
                <div className="flex flex-wrap gap-2">
                    <Input
                        placeholder="Model name (e.g., llama3.3:70b)"
                        value={modelName}
                        onChange={(e) => setModelName(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && !pulling && handlePull()}
                        disabled={pulling}
                        className="flex-1 min-w-[200px]"
                    />
                    {nodes?.length ? (
                        <Select value={targetNode} onValueChange={setTargetNode} disabled={pulling}>
                            <SelectTrigger className="w-[180px]">
                                <Server className="h-4 w-4 mr-2" />
                                <SelectValue placeholder="Pull to..." />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value={PULL_ALL}>All nodes</SelectItem>
                                {nodes?.map((n) => (
                                    <SelectItem key={n.id} value={String(n.id)}>
                                        {n.name}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    ) : null}
                    <Button onClick={handlePull} disabled={!modelName.trim() || pulling}>
                        {pulling ? 'Pulling...' : 'Pull'}
                    </Button>
                </div>
                {nodes?.length ? (
                    <p className="text-xs text-muted-foreground">
                        Pull to a specific node or all nodes. Manage nodes in{' '}
                        <Link href="/nodes" className="text-primary hover:underline">
                            Nodes
                        </Link>
                        .
                    </p>
                ) : null}
                {pulling && (
                    <div className="space-y-2">
                        <Progress value={progress} className="h-2" />
                        <p className="text-xs text-muted-foreground">{status} — %{progress.toFixed(0)}</p>
                    </div>
                )}
            </CardContent>
        </Card>
    );
}

function capabilityBadge(cap: string) {
    switch (cap) {
        case 'tools':
            return <Badge key={cap} variant="outline" className="text-blue-400 border-blue-400/30 bg-blue-400/10 text-xs">Tools</Badge>;
        case 'thinking':
            return <Badge key={cap} variant="outline" className="text-purple-400 border-purple-400/30 bg-purple-400/10 text-xs">Thinking</Badge>;
        case 'vision':
            return <Badge key={cap} variant="outline" className="text-emerald-400 border-emerald-400/30 bg-emerald-400/10 text-xs">Vision</Badge>;
        case 'completion':
            return null;
        default:
            return <Badge key={cap} variant="outline" className="text-xs">{cap}</Badge>;
    }
}

function formatContextLen(n: number | null) {
    if (!n) return '—';
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
    return String(n);
}

export default function OllamaModelsPage() {
    const [search, setSearch] = useState('');
    const queryClient = useQueryClient();
    const { data: models, isLoading } = useQuery({
        queryKey: ['ollama-models'],
        queryFn: ollamaModelsApi.list,
    });

    const deleteMutation = useMutation({
        mutationFn: ollamaModelsApi.delete,
        onSuccess: (_, name) => {
            queryClient.invalidateQueries({ queryKey: ['ollama-models'] });
            toast.success(`${name} deleted`);
        },
        onError: (err) => toast.error(err.message),
    });

    const syncMutation = useMutation({
        mutationFn: ollamaModelsApi.syncCapabilities,
        onSuccess: (result) => {
            queryClient.invalidateQueries({ queryKey: ['ollama-models'] });
            queryClient.invalidateQueries({ queryKey: ['model-mappings'] });
            toast.success(`${result.synced} models synchronized`);
        },
        onError: (err) => toast.error(err.message),
    });

    const filtered = models?.filter((m) =>
        m.name.toLowerCase().includes(search.toLowerCase()) ||
        (m.display_name && m.display_name.toLowerCase().includes(search.toLowerCase()))
    ) || [];

    return (
        <div className="space-y-4">
            <PullModelSection />

            <div className="flex items-center justify-between gap-4">
                <div className="relative flex-1 max-w-sm">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input placeholder="Search models..." value={search} onChange={(e) => setSearch(e.target.value)} className="pl-9" />
                </div>
                <div className="flex items-center gap-2">
                    <Button variant="outline" size="sm" onClick={() => syncMutation.mutate()} disabled={syncMutation.isPending}>
                        <RefreshCw className={`h-4 w-4 mr-2 ${syncMutation.isPending ? 'animate-spin' : ''}`} />
                        Sync Caps
                    </Button>
                    <Badge variant="outline" className="text-muted-foreground">
                        <HardDrive className="h-3 w-3 mr-1" /> {models?.length || 0} models
                    </Badge>
                </div>
            </div>

            <Card>
                <CardContent className="p-0">
                    {isLoading ? (
                        <div className="p-6"><Skeleton className="h-64 w-full" /></div>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Model</TableHead>
                                    <TableHead>Size</TableHead>
                                    <TableHead>Status</TableHead>
                                    <TableHead>Display Name</TableHead>
                                    <TableHead>Nodes</TableHead>
                                    <TableHead>Context Length</TableHead>
                                    <TableHead>Capabilities</TableHead>
                                    <TableHead className="text-right">Action</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {filtered.map((m) => (
                                    <TableRow key={m.name}>
                                        <TableCell className="font-mono text-sm">{m.name}</TableCell>
                                        <TableCell className="text-sm">{formatSize(m.size)}</TableCell>
                                        <TableCell>
                                            {m.is_mapped ? (
                                                <Badge variant="outline" className="text-emerald-400 border-emerald-400/30 bg-emerald-400/10">
                                                    ✅ Mapped
                                                </Badge>
                                            ) : (
                                                <Badge variant="outline" className="text-amber-400 border-amber-400/30 bg-amber-400/10">
                                                    Unmapped
                                                </Badge>
                                            )}
                                        </TableCell>
                                        <TableCell>
                                            {m.display_name ? (
                                                <span className="text-sm">{m.display_name}</span>
                                            ) : (
                                                <Link href="/models/mappings">
                                                    <Button variant="ghost" size="sm" className="text-xs text-blue-400">Map →</Button>
                                                </Link>
                                            )}
                                        </TableCell>
                                        <TableCell>
                                            {m.nodes && m.nodes.length > 0 ? (
                                                <span className="text-xs text-muted-foreground">{m.nodes.join(', ')}</span>
                                            ) : (
                                                <span className="text-xs text-muted-foreground">—</span>
                                            )}
                                        </TableCell>
                                        <TableCell className="text-sm">{formatContextLen(m.context_length)}</TableCell>
                                        <TableCell>
                                            {m.capabilities && m.capabilities.length > 0 ? (
                                                <div className="flex flex-wrap gap-1">
                                                    {m.capabilities.filter(c => c !== 'completion').map((cap) => capabilityBadge(cap))}
                                                </div>
                                            ) : (
                                                <span className="text-xs text-muted-foreground">—</span>
                                            )}
                                        </TableCell>
                                        <TableCell className="text-right">
                                            <Dialog>
                                                <DialogTrigger asChild>
                                                    <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive">
                                                        <Trash2 className="h-4 w-4" />
                                                    </Button>
                                                </DialogTrigger>
                                                <DialogContent>
                                                    <DialogHeader><DialogTitle>Delete Model</DialogTitle></DialogHeader>
                                                    <p className="text-sm text-muted-foreground py-4">
                                                        Are you sure you want to delete model <strong>{m.name}</strong> from Ollama?
                                                    </p>
                                                    <DialogFooter>
                                                        <DialogClose asChild><Button variant="ghost">Cancel</Button></DialogClose>
                                                        <DialogClose asChild>
                                                            <Button variant="destructive" onClick={() => deleteMutation.mutate(m.name)}>Delete</Button>
                                                        </DialogClose>
                                                    </DialogFooter>
                                                </DialogContent>
                                            </Dialog>
                                        </TableCell>
                                    </TableRow>
                                ))}
                                {filtered.length === 0 && (
                                    <TableRow>
                                        <TableCell colSpan={8} className="text-center text-muted-foreground py-12">
                                            {search ? 'No results found' : 'No models found on Ollama server'}
                                        </TableCell>
                                    </TableRow>
                                )}
                            </TableBody>
                        </Table>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}

'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { vllmModelsApi, nodesApi, type VllmModel } from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { useState } from 'react';
import { Search, HardDrive, RefreshCw } from 'lucide-react';
import Link from 'next/link';
import { toast } from 'sonner';

function formatSize(bytes: number | null) {
    if (!bytes) return '—';
    if (bytes >= 1_000_000_000) return `${(bytes / 1_000_000_000).toFixed(1)} GB`;
    if (bytes >= 1_000_000) return `${(bytes / 1_000_000).toFixed(0)} MB`;
    return `${(bytes / 1_000).toFixed(0)} KB`;
}

function formatContextLen(n: number | null) {
    if (!n) return '—';
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
    return String(n);
}

export default function VllmModelsPage() {
    const [search, setSearch] = useState('');
    const queryClient = useQueryClient();
    const { data: models, isLoading } = useQuery({
        queryKey: ['vllm-models'],
        queryFn: vllmModelsApi.list,
    });

    const syncMutation = useMutation({
        mutationFn: vllmModelsApi.syncMeta,
        onSuccess: (result) => {
            queryClient.invalidateQueries({ queryKey: ['vllm-models'] });
            toast.success(`${result.synced} vLLM models synchronized`);
        },
        onError: (err: Error) => toast.error(err.message),
    });

    const toggleMutation = useMutation({
        mutationFn: ({ nodeId, modelName, isAvailable }: { nodeId: number; modelName: string; isAvailable: boolean }) =>
            nodesApi.toggleModelAvailable(nodeId, modelName, isAvailable),
        onSuccess: (data) => {
            queryClient.invalidateQueries({ queryKey: ['vllm-models'] });
            queryClient.invalidateQueries({ queryKey: ['nodes'] });
            toast.success(`${data.model_name} is now ${data.is_available ? 'active' : 'inactive'}`);
        },
        onError: (err: Error) => toast.error(err.message),
    });

    const filtered = models?.filter((m) =>
        m.name.toLowerCase().includes(search.toLowerCase()) ||
        m.node_name.toLowerCase().includes(search.toLowerCase()) ||
        (m.display_name && m.display_name.toLowerCase().includes(search.toLowerCase()))
    ) || [];

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between gap-4">
                <div className="relative flex-1 max-w-sm">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input placeholder="Search models..." value={search} onChange={(e) => setSearch(e.target.value)} className="pl-9" />
                </div>
                <div className="flex items-center gap-2">
                    <Button variant="outline" size="sm" onClick={() => syncMutation.mutate()} disabled={syncMutation.isPending}>
                        <RefreshCw className={`h-4 w-4 mr-2 ${syncMutation.isPending ? 'animate-spin' : ''}`} />
                        Sync Meta
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
                                    <TableHead>Node</TableHead>
                                    <TableHead>Size</TableHead>
                                    <TableHead>Status</TableHead>
                                    <TableHead>Display Name</TableHead>
                                    <TableHead>Context Length</TableHead>
                                    <TableHead>Max Model Len</TableHead>
                                    <TableHead>Capabilities</TableHead>
                                    <TableHead className="text-right">Available</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {filtered.map((m) => (
                                    <TableRow key={`${m.name}-${m.node_id}`} className={m.is_available ? '' : 'opacity-50 bg-muted/20'}>
                                        <TableCell className="font-mono text-sm">{m.name}</TableCell>
                                        <TableCell className="text-sm">{m.node_name}</TableCell>
                                        <TableCell className="text-sm">{formatSize(m.model_size)}</TableCell>
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
                                        <TableCell className="text-sm">{formatContextLen(m.context_length)}</TableCell>
                                        <TableCell className="text-sm">{formatContextLen(m.max_model_len)}</TableCell>
                                        <TableCell>
                                            {m.capabilities && m.capabilities.length > 0 ? (
                                                <div className="flex flex-wrap gap-1">
                                                    {m.capabilities.map((cap) => (
                                                        <Badge key={cap} variant="secondary" className="text-[10px] px-1.5 py-0">
                                                            {cap}
                                                        </Badge>
                                                    ))}
                                                </div>
                                            ) : (
                                                <span className="text-xs text-muted-foreground">—</span>
                                            )}
                                        </TableCell>
                                        <TableCell className="text-right">
                                            <Switch
                                                checked={m.is_available}
                                                onCheckedChange={(checked) => {
                                                    toggleMutation.mutate({
                                                        nodeId: m.node_id,
                                                        modelName: m.name,
                                                        isAvailable: checked,
                                                    });
                                                }}
                                                disabled={toggleMutation.isPending}
                                                aria-label="Toggle availability"
                                            />
                                        </TableCell>
                                    </TableRow>
                                ))}
                                {filtered.length === 0 && (
                                    <TableRow>
                                        <TableCell colSpan={9} className="text-center text-muted-foreground py-12">
                                            {search ? 'No results found' : 'No vLLM models found. Add a vLLM node and run model sync to discover models.'}
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

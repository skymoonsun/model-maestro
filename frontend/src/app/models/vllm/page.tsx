'use client';

import { useQuery } from '@tanstack/react-query';
import { vllmModelsApi } from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { toast } from 'sonner';
import { useState } from 'react';
import { Search, Server, Cpu, Link as LinkIcon } from 'lucide-react';
import Link from 'next/link';
import { Button } from '@/components/ui/button';

function formatSize(bytes: number | null) {
    if (!bytes) return '—';
    if (bytes >= 1_000_000_000) return `${(bytes / 1_000_000_000).toFixed(1)} GB`;
    if (bytes >= 1_000_000) return `${(bytes / 1_000_000).toFixed(0)} MB`;
    return `${(bytes / 1_000).toFixed(0)} KB`;
}

export default function VllmModelsPage() {
    const [search, setSearch] = useState('');
    const { data: models, isLoading } = useQuery({
        queryKey: ['vllm-models'],
        queryFn: vllmModelsApi.list,
    });

    const filtered = models?.filter((m) =>
        m.name.toLowerCase().includes(search.toLowerCase()) ||
        m.node_name.toLowerCase().includes(search.toLowerCase()) ||
        (m.display_name && m.display_name.toLowerCase().includes(search.toLowerCase()))
    ) || [];

    // Group by model name for summary
    const modelGroups = filtered.reduce<Record<string, typeof filtered>>((acc, m) => {
        if (!acc[m.name]) acc[m.name] = [];
        acc[m.name].push(m);
        return acc;
    }, {});

    return (
        <div className="space-y-4">
            <Card>
                <CardHeader className="pb-3">
                    <CardTitle className="text-sm font-medium flex items-center gap-2">
                        <Cpu className="h-4 w-4" /> vLLM Models
                    </CardTitle>
                </CardHeader>
                <CardContent>
                    <p className="text-xs text-muted-foreground">
                        Models discovered from vLLM nodes via health check / model sync.
                        These are served by OpenAI-compatible vLLM endpoints.
                    </p>
                </CardContent>
            </Card>

            <div className="flex items-center justify-between gap-4">
                <div className="relative flex-1 max-w-sm">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input placeholder="Search models..." value={search} onChange={(e) => setSearch(e.target.value)} className="pl-9" />
                </div>
                <Badge variant="outline" className="text-muted-foreground">
                    <Server className="h-3 w-3 mr-1" /> {Object.keys(modelGroups).length} unique / {models?.length || 0} total
                </Badge>
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
                                    <TableHead>Base URL</TableHead>
                                    <TableHead>Size</TableHead>
                                    <TableHead>Family</TableHead>
                                    <TableHead>Status</TableHead>
                                    <TableHead>Display Name</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {filtered.map((m) => (
                                    <TableRow key={`${m.node_id}-${m.name}`}>
                                        <TableCell className="font-mono text-sm">{m.name}</TableCell>
                                        <TableCell className="text-sm">{m.node_name}</TableCell>
                                        <TableCell className="text-xs text-muted-foreground font-mono truncate max-w-[200px]" title={m.base_url}>
                                            {m.base_url}
                                        </TableCell>
                                        <TableCell className="text-sm">{formatSize(m.model_size)}</TableCell>
                                        <TableCell className="text-sm">{m.model_family || '—'}</TableCell>
                                        <TableCell>
                                            {m.is_mapped ? (
                                                <Badge variant="outline" className="text-emerald-400 border-emerald-400/30 bg-emerald-400/10">
                                                    Mapped
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
                                                    <Button variant="ghost" size="sm" className="text-xs text-blue-400">
                                                        <LinkIcon className="h-3 w-3 mr-1" /> Map
                                                    </Button>
                                                </Link>
                                            )}
                                        </TableCell>
                                    </TableRow>
                                ))}
                                {filtered.length === 0 && (
                                    <TableRow>
                                        <TableCell colSpan={7} className="text-center text-muted-foreground py-12">
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

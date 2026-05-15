'use client';

import { useQuery } from '@tanstack/react-query';
import { antigravityModelsApi } from '@/lib/api';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { useState } from 'react';
import { Search, HardDrive } from 'lucide-react';
import Link from 'next/link';
import { Button } from '@/components/ui/button';

function formatContextLen(n: number | null) {
    if (!n) return '—';
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
    return String(n);
}

export default function AntigravityModelsPage() {
    const [search, setSearch] = useState('');
    const { data: models, isLoading } = useQuery({
        queryKey: ['antigravity-models'],
        queryFn: antigravityModelsApi.list,
    });

    const filtered = models?.filter((m) =>
        m.name.toLowerCase().includes(search.toLowerCase()) ||
        m.node_name.toLowerCase().includes(search.toLowerCase()) ||
        (m.display_name && m.display_name.toLowerCase().includes(search.toLowerCase()))
    ) || [];

    const modelGroups = filtered.reduce<Record<string, {
        name: string;
        model_size: number | null;
        model_family: string | null;
        is_mapped: boolean;
        display_name: string | null;
        nodes: string[];
        context_length: number | null;
        capabilities: string[] | null;
    }>>((acc, m) => {
        if (!acc[m.name]) {
            acc[m.name] = {
                name: m.name,
                model_size: m.model_size,
                model_family: m.model_family,
                is_mapped: m.is_mapped,
                display_name: m.display_name,
                nodes: [],
                context_length: m.context_length,
                capabilities: m.capabilities,
            };
        }
        if (!acc[m.name].nodes.includes(m.node_name)) {
            acc[m.name].nodes.push(m.node_name);
        }
        return acc;
    }, {});

    const groupedList = Object.values(modelGroups);

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between gap-4">
                <div className="relative flex-1 max-w-sm">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input placeholder="Search models..." value={search} onChange={(e) => setSearch(e.target.value)} className="pl-9" />
                </div>
                <div className="flex items-center gap-2">
                    <Badge variant="outline" className="text-muted-foreground">
                        <HardDrive className="h-3 w-3 mr-1" /> {groupedList.length || 0} models
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
                                    <TableHead>Status</TableHead>
                                    <TableHead>Display Name</TableHead>
                                    <TableHead>Nodes</TableHead>
                                    <TableHead>Context Length</TableHead>
                                    <TableHead>Capabilities</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {groupedList.map((m) => (
                                    <TableRow key={m.name}>
                                        <TableCell className="font-mono text-sm">{m.name}</TableCell>
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
                                    </TableRow>
                                ))}
                                {groupedList.length === 0 && (
                                    <TableRow>
                                        <TableCell colSpan={6} className="text-center text-muted-foreground py-12">
                                            {search ? 'No results found' : 'No antigravity models found. Add an antigravity node and run model sync to discover models.'}
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

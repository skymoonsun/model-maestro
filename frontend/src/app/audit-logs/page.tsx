'use client';

import { useQuery } from '@tanstack/react-query';
import { auditLogsApi, type AuditLog } from '@/lib/api';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Input } from '@/components/ui/input';
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { useState } from 'react';
import { Search } from 'lucide-react';

function actionBadge(action: string) {
    if (action.includes('create')) return <Badge className="bg-emerald-500/20 text-emerald-400 border-emerald-400/30 text-xs">{action}</Badge>;
    if (action.includes('delete')) return <Badge className="bg-red-500/20 text-red-400 border-red-400/30 text-xs">{action}</Badge>;
    if (action.includes('update')) return <Badge className="bg-blue-500/20 text-blue-400 border-blue-400/30 text-xs">{action}</Badge>;
    return <Badge variant="outline" className="text-xs">{action}</Badge>;
}

export default function AuditLogsPage() {
    const [search, setSearch] = useState('');
    const { data: logs, isLoading } = useQuery({
        queryKey: ['audit-logs'],
        queryFn: () => auditLogsApi.list({ limit: 100 }),
    });

    const logsArray = (Array.isArray(logs) ? logs : ((logs as any)?.logs || [])) as AuditLog[];
    const filtered = logsArray.filter((l: AuditLog) =>
        (l.admin_action || '').toLowerCase().includes(search.toLowerCase()) ||
        (l.target_type || '').toLowerCase().includes(search.toLowerCase()) ||
        (l.target_id || '').toLowerCase().includes(search.toLowerCase())
    );

    return (
        <div className="space-y-4">
            <div className="relative max-w-sm">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                <Input placeholder="Search logs..." value={search} onChange={(e) => setSearch(e.target.value)} className="pl-9" />
            </div>

            <Card>
                <CardContent className="p-0">
                    {isLoading ? (
                        <div className="p-6"><Skeleton className="h-64 w-full" /></div>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Action</TableHead>
                                    <TableHead>Target Type</TableHead>
                                    <TableHead>Target ID</TableHead>
                                    <TableHead>Details</TableHead>
                                    <TableHead>Time</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {filtered.map((log) => (
                                    <TableRow key={log.id}>
                                        <TableCell>{actionBadge(log.admin_action)}</TableCell>
                                        <TableCell className="text-sm">{log.target_type}</TableCell>
                                        <TableCell className="font-mono text-xs">{log.target_id}</TableCell>
                                        <TableCell className="text-xs text-muted-foreground max-w-xs truncate">
                                            {log.details ? JSON.stringify(log.details).slice(0, 80) : '—'}
                                        </TableCell>
                                        <TableCell className="text-xs text-muted-foreground whitespace-nowrap">
                                            {new Date(log.created_at).toLocaleString('en-US')}
                                        </TableCell>
                                    </TableRow>
                                ))}
                                {filtered.length === 0 && (
                                    <TableRow>
                                        <TableCell colSpan={5} className="text-center text-muted-foreground py-12">
                                            {search ? 'No results found' : 'No logs yet'}
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

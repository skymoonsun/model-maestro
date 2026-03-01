'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { modelMappingsApi, ollamaModelsApi, type ModelMapping, type CreateModelMapping } from '@/lib/api';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import {
    Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter, DialogClose,
} from '@/components/ui/dialog';
import { toast } from 'sonner';
import { useState } from 'react';
import { Plus, Trash2, RefreshCw, Search } from 'lucide-react';

function capabilityBadge(cap: string) {
    switch (cap) {
        case 'tools':
            return <Badge key={cap} variant="outline" className="text-blue-400 border-blue-400/30 bg-blue-400/10 text-xs">🔧 Tools</Badge>;
        case 'thinking':
            return <Badge key={cap} variant="outline" className="text-purple-400 border-purple-400/30 bg-purple-400/10 text-xs">🧠 Thinking</Badge>;
        case 'vision':
            return <Badge key={cap} variant="outline" className="text-emerald-400 border-emerald-400/30 bg-emerald-400/10 text-xs">👁 Vision</Badge>;
        case 'completion':
            return null; // completion is on every model, skip
        default:
            return <Badge key={cap} variant="outline" className="text-xs">{cap}</Badge>;
    }
}

function CreateMappingDialog() {
    const [form, setForm] = useState<CreateModelMapping>({
        display_name: '', real_name: '', context_length: '', capabilities: [],
    });
    const [open, setOpen] = useState(false);
    const queryClient = useQueryClient();

    const mutation = useMutation({
        mutationFn: (data: CreateModelMapping) => modelMappingsApi.create(data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['model-mappings'] });
            toast.success('Mapping created');
            setForm({ display_name: '', real_name: '', context_length: '', capabilities: [] });
            setOpen(false);
        },
        onError: (err) => toast.error(err.message),
    });

    const toggleCap = (cap: string) => {
        setForm((prev) => ({
            ...prev,
            capabilities: prev.capabilities?.includes(cap)
                ? prev.capabilities.filter((c) => c !== cap)
                : [...(prev.capabilities || []), cap],
        }));
    };

    return (
        <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
                <Button size="sm"><Plus className="h-4 w-4 mr-2" /> New Mapping</Button>
            </DialogTrigger>
            <DialogContent>
                <DialogHeader><DialogTitle>New Model Mapping</DialogTitle></DialogHeader>
                <div className="space-y-4 py-4">
                    <div>
                        <label className="text-sm text-muted-foreground">Display Name</label>
                        <Input value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} placeholder="qwen3.5:latest" />
                    </div>
                    <div>
                        <label className="text-sm text-muted-foreground">Real Name (Ollama)</label>
                        <Input value={form.real_name} onChange={(e) => setForm({ ...form, real_name: e.target.value })} placeholder="qwen3.5:cloud" />
                    </div>
                    <div>
                        <label className="text-sm text-muted-foreground">Context Length</label>
                        <Input value={form.context_length} onChange={(e) => setForm({ ...form, context_length: e.target.value })} placeholder="256K" />
                    </div>
                    <div>
                        <label className="text-sm text-muted-foreground mb-2 block">Capabilities</label>
                        <div className="flex gap-2">
                            {['tools', 'thinking', 'vision'].map((cap) => (
                                <Button key={cap} size="sm" variant={form.capabilities?.includes(cap) ? 'default' : 'outline'}
                                    onClick={() => toggleCap(cap)}
                                >
                                    {cap === 'tools' ? '🔧 Tools' : cap === 'thinking' ? '🧠 Thinking' : '👁 Vision'}
                                </Button>
                            ))}
                        </div>
                    </div>
                </div>
                <DialogFooter>
                    <DialogClose asChild><Button variant="ghost">Cancel</Button></DialogClose>
                    <Button onClick={() => mutation.mutate(form)} disabled={mutation.isPending || !form.display_name || !form.real_name}>
                        {mutation.isPending ? 'Creating...' : 'Create'}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

export default function ModelMappingsPage() {
    const [search, setSearch] = useState('');
    const queryClient = useQueryClient();
    const { data: mappings, isLoading } = useQuery({
        queryKey: ['model-mappings'],
        queryFn: modelMappingsApi.list,
    });

    const deleteMutation = useMutation({
        mutationFn: modelMappingsApi.delete,
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['model-mappings'] });
            toast.success('Mapping deleted');
        },
        onError: (err) => toast.error(err.message),
    });

    const syncMutation = useMutation({
        mutationFn: ollamaModelsApi.syncCapabilities,
        onSuccess: (result) => {
            queryClient.invalidateQueries({ queryKey: ['model-mappings'] });
            toast.success(`${result.synced} models synchronized`);
        },
        onError: (err) => toast.error(err.message),
    });

    const filtered = mappings?.filter((m) =>
        m.display_name.toLowerCase().includes(search.toLowerCase()) ||
        m.real_name.toLowerCase().includes(search.toLowerCase())
    ) || [];

    if (isLoading) return <Card><CardContent className="p-6"><Skeleton className="h-64 w-full" /></CardContent></Card>;

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between gap-4">
                <div className="relative flex-1 max-w-sm">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input placeholder="Search models..." value={search} onChange={(e) => setSearch(e.target.value)} className="pl-9" />
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={() => syncMutation.mutate()} disabled={syncMutation.isPending}>
                        <RefreshCw className={`h-4 w-4 mr-2 ${syncMutation.isPending ? 'animate-spin' : ''}`} />
                        Sync Caps
                    </Button>
                    <CreateMappingDialog />
                </div>
            </div>

            <Card>
                <CardContent className="p-0">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Display Name</TableHead>
                                <TableHead>Real Name</TableHead>
                                <TableHead>Context</TableHead>
                                <TableHead>Capabilities</TableHead>
                                <TableHead className="text-right">Action</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {filtered.map((m) => (
                                <TableRow key={m.display_name}>
                                    <TableCell className="font-medium">{m.display_name}</TableCell>
                                    <TableCell className="font-mono text-xs text-muted-foreground">{m.real_name}</TableCell>
                                    <TableCell>
                                        <Badge variant="outline">{m.context_length_display || `${(m.context_length / 1024).toFixed(0)}K`}</Badge>
                                    </TableCell>
                                    <TableCell>
                                        <div className="flex gap-1 flex-wrap">
                                            {m.capabilities?.filter(c => c !== 'completion').map((c) => capabilityBadge(c))}
                                            {(!m.capabilities || m.capabilities.filter(c => c !== 'completion').length === 0) && (
                                                <span className="text-xs text-muted-foreground">—</span>
                                            )}
                                        </div>
                                    </TableCell>
                                    <TableCell className="text-right">
                                        <Dialog>
                                            <DialogTrigger asChild>
                                                <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive">
                                                    <Trash2 className="h-4 w-4" />
                                                </Button>
                                            </DialogTrigger>
                                            <DialogContent>
                                                <DialogHeader><DialogTitle>Delete Mapping</DialogTitle></DialogHeader>
                                                <p className="text-sm text-muted-foreground py-4">
                                                    Are you sure you want to delete mapping <strong>{m.display_name}</strong>?
                                                </p>
                                                <DialogFooter>
                                                    <DialogClose asChild><Button variant="ghost">Cancel</Button></DialogClose>
                                                    <DialogClose asChild>
                                                        <Button variant="destructive" onClick={() => deleteMutation.mutate(m.display_name)}>Delete</Button>
                                                    </DialogClose>
                                                </DialogFooter>
                                            </DialogContent>
                                        </Dialog>
                                    </TableCell>
                                </TableRow>
                            ))}
                            {filtered.length === 0 && (
                                <TableRow>
                                    <TableCell colSpan={5} className="text-center text-muted-foreground py-12">
                                        {search ? 'No results found' : 'No mappings yet'}
                                    </TableCell>
                                </TableRow>
                            )}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>
        </div>
    );
}

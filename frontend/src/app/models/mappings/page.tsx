'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { modelMappingsApi, ollamaModelsApi, nodesApi, type ModelMapping, type CreateModelMapping } from '@/lib/api';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
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
import { useState, useMemo } from 'react';
import { Plus, Trash2, RefreshCw, Search, Server, Filter, X, Pencil } from 'lucide-react';

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

function MappingForm({
    form,
    setForm,
    nodes,
}: {
    form: CreateModelMapping;
    setForm: React.Dispatch<React.SetStateAction<CreateModelMapping>>;
    nodes: { id: number; name: string }[];
}) {
    const toggleCap = (cap: string) => {
        setForm((prev) => ({
            ...prev,
            capabilities: prev.capabilities?.includes(cap)
                ? prev.capabilities.filter((c) => c !== cap)
                : [...(prev.capabilities || []), cap],
        }));
    };

    return (
        <div className="space-y-4 py-4">
            <div>
                <label className="text-sm text-muted-foreground">Display Name</label>
                <Input value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} placeholder="qwen3.5:latest" />
            </div>
            <div>
                <label className="text-sm text-muted-foreground">Real Name (Ollama / vLLM)</label>
                <Input value={form.real_name} onChange={(e) => setForm({ ...form, real_name: e.target.value })} placeholder="qwen3.5:cloud" />
            </div>
            <div>
                <label className="text-sm text-muted-foreground mb-2 block">Nodes (optional)</label>
                <p className="text-xs text-muted-foreground mb-2">Seçilmezse mapping tüm node’larda geçerlidir. Birden fazla seçebilirsiniz.</p>
                <div className="max-h-40 overflow-y-auto rounded-md border p-2 space-y-2">
                    {nodes.length === 0 ? (
                        <span className="text-xs text-muted-foreground">No nodes</span>
                    ) : (
                        nodes.map((n) => {
                            const set = new Set(form.node_ids || []);
                            const checked = set.has(n.id);
                            return (
                                <label key={n.id} className="flex items-center gap-2 text-sm cursor-pointer">
                                    <input
                                        type="checkbox"
                                        className="rounded border-input"
                                        checked={checked}
                                        onChange={() => {
                                            const next = new Set(form.node_ids || []);
                                            if (checked) next.delete(n.id);
                                            else next.add(n.id);
                                            setForm({ ...form, node_ids: Array.from(next) });
                                        }}
                                    />
                                    <Server className="h-3 w-3 shrink-0 text-muted-foreground" />
                                    <span>{n.name}</span>
                                </label>
                            );
                        })
                    )}
                </div>
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
                            {cap === 'tools' ? 'Tools' : cap === 'thinking' ? 'Thinking' : 'Vision'}
                        </Button>
                    ))}
                </div>
            </div>
        </div>
    );
}

function CreateMappingDialog({ nodes }: { nodes: { id: number; name: string }[] }) {
    const [form, setForm] = useState<CreateModelMapping>({
        display_name: '',
        real_name: '',
        context_length: '',
        capabilities: [],
        node_ids: [],
    });
    const [open, setOpen] = useState(false);
    const queryClient = useQueryClient();

    const mutation = useMutation({
        mutationFn: (data: CreateModelMapping) => modelMappingsApi.create(data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['model-mappings'] });
            toast.success('Mapping created');
            setForm({
                display_name: '',
                real_name: '',
                context_length: '',
                capabilities: [],
                node_ids: [],
            });
            setOpen(false);
        },
        onError: (err: Error) => toast.error(err.message),
    });

    return (
        <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
                <Button size="sm"><Plus className="h-4 w-4 mr-2" /> New Mapping</Button>
            </DialogTrigger>
            <DialogContent>
                <DialogHeader><DialogTitle>New Model Mapping</DialogTitle></DialogHeader>
                <MappingForm form={form} setForm={setForm} nodes={nodes} />
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

function EditMappingDialog({ mapping, nodes }: { mapping: ModelMapping; nodes: { id: number; name: string }[] }) {
    const [form, setForm] = useState<CreateModelMapping>({
        display_name: mapping.display_name,
        real_name: mapping.real_name,
        node_ids:
            mapping.node_ids?.length
                ? [...mapping.node_ids]
                : mapping.node_id != null
                  ? [mapping.node_id]
                  : [],
        context_length: mapping.context_length_display || String(mapping.context_length || ''),
        capabilities: mapping.capabilities || [],
    });
    const [open, setOpen] = useState(false);
    const queryClient = useQueryClient();

    const mutation = useMutation({
        mutationFn: (data: CreateModelMapping) => modelMappingsApi.update(mapping.display_name, data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['model-mappings'] });
            toast.success('Mapping updated');
            setOpen(false);
        },
        onError: (err: Error) => toast.error(err.message),
    });

    return (
        <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
                <Button variant="ghost" size="icon" className="h-8 w-8">
                    <Pencil className="h-4 w-4" />
                </Button>
            </DialogTrigger>
            <DialogContent>
                <DialogHeader><DialogTitle>Edit Model Mapping</DialogTitle></DialogHeader>
                <MappingForm form={form} setForm={setForm} nodes={nodes} />
                <DialogFooter>
                    <DialogClose asChild><Button variant="ghost">Cancel</Button></DialogClose>
                    <Button onClick={() => mutation.mutate(form)} disabled={mutation.isPending || !form.display_name || !form.real_name}>
                        {mutation.isPending ? 'Saving...' : 'Save'}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

export default function ModelMappingsPage() {
    const [search, setSearch] = useState('');
    const [nodeFilter, setNodeFilter] = useState<string>('all');
    const [capFilter, setCapFilter] = useState<string>('all');
    const [ctxMin, setCtxMin] = useState<string>('');
    const [showFilters, setShowFilters] = useState(false);
    const queryClient = useQueryClient();

    const { data: mappings, isLoading } = useQuery({
        queryKey: ['model-mappings'],
        queryFn: modelMappingsApi.list,
    });

    const { data: nodes } = useQuery({
        queryKey: ['nodes'],
        queryFn: () => nodesApi.list(true),
    });

    const deleteMutation = useMutation({
        mutationFn: modelMappingsApi.delete,
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['model-mappings'] });
            toast.success('Mapping deleted');
        },
        onError: (err: Error) => toast.error(err.message),
    });

    const syncMutation = useMutation({
        mutationFn: ollamaModelsApi.syncCapabilities,
        onSuccess: (result) => {
            queryClient.invalidateQueries({ queryKey: ['model-mappings'] });
            toast.success(`${result.synced} models synchronized`);
        },
        onError: (err: Error) => toast.error(err.message),
    });

    const activeFilters = useMemo(() => {
        const count = [
            search ? 1 : 0,
            nodeFilter !== 'all' ? 1 : 0,
            capFilter !== 'all' ? 1 : 0,
            ctxMin ? 1 : 0,
        ].reduce((a, b) => a + b, 0);
        return count;
    }, [search, nodeFilter, capFilter, ctxMin]);

    const filtered = useMemo(() => {
        if (!mappings) return [];
        return mappings.filter((m) => {
            const matchesSearch =
                m.display_name.toLowerCase().includes(search.toLowerCase()) ||
                m.real_name.toLowerCase().includes(search.toLowerCase());
            const matchesNode =
                nodeFilter === 'all' ||
                (m.node_ids?.includes(Number(nodeFilter)) ?? false) ||
                (m.node_id != null && String(m.node_id) === nodeFilter);
            const matchesCap = capFilter === 'all' || m.capabilities?.includes(capFilter);
            const matchesCtx = !ctxMin || (m.context_length && m.context_length >= Number(ctxMin));
            return matchesSearch && matchesNode && matchesCap && matchesCtx;
        });
    }, [mappings, search, nodeFilter, capFilter, ctxMin]);

    if (isLoading) return <Card><CardContent className="p-6"><Skeleton className="h-64 w-full" /></CardContent></Card>;

    const nodeOptions = nodes?.map((n) => ({ id: n.id, name: n.name })) || [];

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between gap-4">
                <div className="relative flex-1 max-w-sm">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input placeholder="Search models..." value={search} onChange={(e) => setSearch(e.target.value)} className="pl-9" />
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={() => setShowFilters((v) => !v)}>
                        <Filter className="h-4 w-4 mr-2" />
                        Filters
                        {activeFilters > 0 && (
                            <Badge variant="secondary" className="ml-2 text-xs">{activeFilters}</Badge>
                        )}
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => syncMutation.mutate()} disabled={syncMutation.isPending}>
                        <RefreshCw className={`h-4 w-4 mr-2 ${syncMutation.isPending ? 'animate-spin' : ''}`} />
                        Sync Caps
                    </Button>
                    <CreateMappingDialog nodes={nodeOptions} />
                </div>
            </div>

            {showFilters && (
                <Card>
                    <CardContent className="py-4 flex flex-wrap gap-4 items-end">
                        <div className="w-48">
                            <label className="text-xs text-muted-foreground mb-1 block">Node</label>
                            <Select value={nodeFilter} onValueChange={setNodeFilter}>
                                <SelectTrigger>
                                    <SelectValue placeholder="All nodes" />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="all">All nodes</SelectItem>
                                    {nodes?.map((n) => (
                                        <SelectItem key={n.id} value={String(n.id)}>{n.name}</SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="w-48">
                            <label className="text-xs text-muted-foreground mb-1 block">Capability</label>
                            <Select value={capFilter} onValueChange={setCapFilter}>
                                <SelectTrigger>
                                    <SelectValue placeholder="Any capability" />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="all">Any capability</SelectItem>
                                    <SelectItem value="tools">Tools</SelectItem>
                                    <SelectItem value="thinking">Thinking</SelectItem>
                                    <SelectItem value="vision">Vision</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="w-48">
                            <label className="text-xs text-muted-foreground mb-1 block">Min Context (tokens)</label>
                            <Input
                                type="number"
                                placeholder="e.g. 32768"
                                value={ctxMin}
                                onChange={(e) => setCtxMin(e.target.value)}
                            />
                        </div>
                        <Button variant="ghost" size="sm" onClick={() => { setSearch(''); setNodeFilter('all'); setCapFilter('all'); setCtxMin(''); }}>
                            <X className="h-4 w-4 mr-1" /> Reset
                        </Button>
                    </CardContent>
                </Card>
            )}

            <Card>
                <CardContent className="p-0">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Display Name</TableHead>
                                <TableHead>Real Name</TableHead>
                                <TableHead>Provider</TableHead>
                                <TableHead>Nodes</TableHead>
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
                                        {m.node_type ? (
                                            <Badge variant="outline" className="text-xs capitalize">{m.node_type}</Badge>
                                        ) : (
                                            <span className="text-xs text-muted-foreground">—</span>
                                        )}
                                    </TableCell>
                                    <TableCell>
                                        {(m.node_ids?.length ?? 0) > 0 ? (
                                            <div className="flex flex-wrap gap-1">
                                                {(m.node_names?.length ? m.node_names : m.node_ids!.map((id) => `#${id}`)).map((label, i) => (
                                                    <Badge key={`${label}-${i}`} variant="outline" className="text-xs flex items-center gap-1">
                                                        <Server className="h-3 w-3" />
                                                        {label}
                                                    </Badge>
                                                ))}
                                            </div>
                                        ) : m.node_id ? (
                                            <Badge variant="outline" className="text-xs flex items-center gap-1">
                                                <Server className="h-3 w-3" />
                                                {m.node_name || `Node ${m.node_id}`}
                                            </Badge>
                                        ) : (
                                            <span className="text-xs text-muted-foreground">All nodes</span>
                                        )}
                                    </TableCell>
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
                                        <div className="flex items-center justify-end gap-1">
                                            <EditMappingDialog mapping={m} nodes={nodeOptions} />
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
                                        </div>
                                    </TableCell>
                                </TableRow>
                            ))}
                            {filtered.length === 0 && (
                                <TableRow>
                                    <TableCell colSpan={7} className="text-center text-muted-foreground py-12">
                                        {search || nodeFilter !== 'all' || capFilter !== 'all' || ctxMin ? 'No results match your filters' : 'No mappings yet'}
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

'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { modelConfigApi, toolSetsApi, type ModelConfig, type CreateModelConfig, type ToolSet } from '@/lib/api';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import { Skeleton } from '@/components/ui/skeleton';
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import {
    Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter, DialogClose,
} from '@/components/ui/dialog';
import {
    Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { toast } from 'sonner';
import { useState } from 'react';
import { Plus, Trash2, Pencil } from 'lucide-react';

function ConfigFormDialog({ config, onClose }: { config?: ModelConfig; onClose: () => void }) {
    const queryClient = useQueryClient();
    const { data: toolSets } = useQuery({ queryKey: ['tool-sets'], queryFn: toolSetsApi.list });
    const [form, setForm] = useState<CreateModelConfig>({
        model_prefix: config?.model_prefix || '',
        is_exact_match: config?.is_exact_match ?? false,
        allowed_tools: config?.allowed_tools || null,
        unsupported_params: config?.unsupported_params || null,
        default_context_length: config?.default_context_length || null,
        is_active: config?.is_active ?? true,
        maintenance_mode: config?.maintenance_mode ?? false,
        description: config?.description || '',
    });

    const createMutation = useMutation({
        mutationFn: (data: CreateModelConfig) => modelConfigApi.create(data),
        onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['model-config'] }); toast.success('Config created'); onClose(); },
        onError: (err) => toast.error(err.message),
    });
    const updateMutation = useMutation({
        mutationFn: (data: CreateModelConfig) => modelConfigApi.update(config!.id, data),
        onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['model-config'] }); toast.success('Config updated'); onClose(); },
        onError: (err) => toast.error(err.message),
    });

    const handleSave = () => config ? updateMutation.mutate(form) : createMutation.mutate(form);
    const isPending = createMutation.isPending || updateMutation.isPending;

    return (
        <DialogContent className="max-w-lg">
            <DialogHeader><DialogTitle>{config ? 'Edit Config' : 'New Config'}</DialogTitle></DialogHeader>
            <div className="space-y-4 py-4">
                <div>
                    <label className="text-sm text-muted-foreground">Target Pattern (Prefix or Exact Model)</label>
                    <Input value={form.model_prefix} onChange={(e) => setForm({ ...form, model_prefix: e.target.value })} placeholder="deepseek OR deepseek-coder:33b" disabled={!!config} />
                    <div className="flex items-center gap-2 mt-2">
                        <Switch checked={form.is_exact_match} onCheckedChange={(v) => setForm({ ...form, is_exact_match: v })} disabled={!!config} />
                        <span className="text-xs text-muted-foreground">Exact Match Only (disables prefix matching)</span>
                    </div>
                </div>
                <div>
                    <label className="text-sm text-muted-foreground">Tool Set / Allowed Tools</label>
                    <Select
                        value={form.allowed_tools === null ? 'full' : 'custom'}
                        onValueChange={(v) => {
                            if (v === 'full') setForm({ ...form, allowed_tools: null });
                            else {
                                const ts = toolSets?.find((t) => t.name === v);
                                if (ts) setForm({ ...form, allowed_tools: ts.tools });
                            }
                        }}
                    >
                        <SelectTrigger><SelectValue /></SelectTrigger>
                        <SelectContent>
                            <SelectItem value="full">Full (No limits)</SelectItem>
                            {toolSets?.map((ts) => (
                                <SelectItem key={ts.id} value={ts.name}>{ts.name} ({ts.tools ? ts.tools.length : '∞'} tool)</SelectItem>
                            ))}
                            <SelectItem value="custom">Custom</SelectItem>
                        </SelectContent>
                    </Select>
                </div>
                <div>
                    <label className="text-sm text-muted-foreground">Unsupported Params (comma separated)</label>
                    <Input
                        value={form.unsupported_params?.join(', ') || ''}
                        onChange={(e) => setForm({ ...form, unsupported_params: e.target.value ? e.target.value.split(',').map(s => s.trim()) : null })}
                        placeholder="tools, tool_choice"
                    />
                </div>
                <div>
                    <label className="text-sm text-muted-foreground">Default Context Length</label>
                    <Input
                        type="number" value={form.default_context_length || ''}
                        onChange={(e) => setForm({ ...form, default_context_length: e.target.value ? parseInt(e.target.value) : null })}
                        placeholder="204800"
                    />
                </div>
                <div>
                    <label className="text-sm text-muted-foreground">Description</label>
                    <Textarea value={form.description || ''} onChange={(e) => setForm({ ...form, description: e.target.value })} />
                </div>
                <div className="flex items-center gap-6">
                    <div className="flex items-center gap-2">
                        <Switch checked={form.is_active} onCheckedChange={(v) => setForm({ ...form, is_active: v })} />
                        <span className="text-sm">Active</span>
                    </div>
                    <div className="flex items-center gap-2">
                        <Switch checked={form.maintenance_mode} onCheckedChange={(v) => setForm({ ...form, maintenance_mode: v })} />
                        <span className="text-sm">Maintenance Mode</span>
                    </div>
                </div>
            </div>
            <DialogFooter>
                <DialogClose asChild><Button variant="ghost">Cancel</Button></DialogClose>
                <Button onClick={handleSave} disabled={isPending || !form.model_prefix}>{isPending ? 'Saving...' : 'Save'}</Button>
            </DialogFooter>
        </DialogContent>
    );
}

export default function ModelConfigPage() {
    const queryClient = useQueryClient();
    const [editingConfig, setEditingConfig] = useState<ModelConfig | undefined>(undefined);
    const [showDialog, setShowDialog] = useState(false);
    const { data: configs, isLoading } = useQuery({
        queryKey: ['model-config'],
        queryFn: modelConfigApi.list,
    });

    const deleteMutation = useMutation({
        mutationFn: modelConfigApi.delete,
        onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['model-config'] }); toast.success('Config deleted'); },
        onError: (err) => toast.error(err.message),
    });

    if (isLoading) return <Card><CardContent className="p-6"><Skeleton className="h-64 w-full" /></CardContent></Card>;

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-end">
                <Dialog open={showDialog} onOpenChange={(o) => { setShowDialog(o); if (!o) setEditingConfig(undefined); }}>
                    <DialogTrigger asChild>
                        <Button size="sm"><Plus className="h-4 w-4 mr-2" /> New Config</Button>
                    </DialogTrigger>
                    <ConfigFormDialog config={editingConfig} onClose={() => setShowDialog(false)} />
                </Dialog>
            </div>

            <Card>
                <CardContent className="p-0">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Target Pattern</TableHead>
                                <TableHead>Match Type</TableHead>
                                <TableHead>Tools</TableHead>
                                <TableHead>Unsupported Params</TableHead>
                                <TableHead>Context</TableHead>
                                <TableHead>Status</TableHead>
                                <TableHead className="text-right">Action</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {configs?.map((c) => (
                                <TableRow key={c.id}>
                                    <TableCell className="font-medium">{c.model_prefix}</TableCell>
                                    <TableCell>
                                        <Badge variant="secondary" className="text-xs">
                                            {c.is_exact_match ? 'Exact' : 'Prefix'}
                                        </Badge>
                                    </TableCell>
                                    <TableCell>
                                        {c.allowed_tools === null ? (
                                            <Badge variant="outline">full</Badge>
                                        ) : (
                                            <Badge variant="outline">{c.allowed_tools.length} tools</Badge>
                                        )}
                                    </TableCell>
                                    <TableCell className="text-xs text-muted-foreground">
                                        {c.unsupported_params ? c.unsupported_params.join(', ') : '—'}
                                    </TableCell>
                                    <TableCell>
                                        {c.default_context_length ? (
                                            <Badge variant="outline">{(c.default_context_length / 1024).toFixed(0)}K</Badge>
                                        ) : '—'}
                                    </TableCell>
                                    <TableCell>
                                        {c.maintenance_mode ? (
                                            <Badge variant="destructive" className="text-xs">Maintenance</Badge>
                                        ) : c.is_active ? (
                                            <Badge variant="outline" className="text-emerald-400 border-emerald-400/30 bg-emerald-400/10 text-xs">Active</Badge>
                                        ) : (
                                            <Badge variant="secondary" className="text-xs">Inactive</Badge>
                                        )}
                                    </TableCell>
                                    <TableCell className="text-right">
                                        <div className="flex items-center justify-end gap-1">
                                            <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => { setEditingConfig(c); setShowDialog(true); }}>
                                                <Pencil className="h-4 w-4" />
                                            </Button>
                                            <Dialog>
                                                <DialogTrigger asChild>
                                                    <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive"><Trash2 className="h-4 w-4" /></Button>
                                                </DialogTrigger>
                                                <DialogContent>
                                                    <DialogHeader><DialogTitle>Delete Config</DialogTitle></DialogHeader>
                                                    <p className="text-sm text-muted-foreground py-4">Are you sure you want to delete config for <strong>{c.model_prefix}</strong>?</p>
                                                    <DialogFooter>
                                                        <DialogClose asChild><Button variant="ghost">Cancel</Button></DialogClose>
                                                        <DialogClose asChild><Button variant="destructive" onClick={() => deleteMutation.mutate(c.id)}>Delete</Button></DialogClose>
                                                    </DialogFooter>
                                                </DialogContent>
                                            </Dialog>
                                        </div>
                                    </TableCell>
                                </TableRow>
                            ))}
                            {(!configs || configs.length === 0) && (
                                <TableRow><TableCell colSpan={6} className="text-center text-muted-foreground py-12">No configs yet</TableCell></TableRow>
                            )}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>
        </div>
    );
}

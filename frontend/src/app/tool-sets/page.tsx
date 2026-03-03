'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toolSetsApi, type ToolSet, type CreateToolSet } from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import {
    Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter, DialogClose,
} from '@/components/ui/dialog';
import { toast } from 'sonner';
import { useState } from 'react';
import { Plus, Trash2, Pencil, Wrench } from 'lucide-react';

function ToolSetFormDialog({ toolSet, onClose }: { toolSet?: ToolSet; onClose: () => void }) {
    const queryClient = useQueryClient();
    const [form, setForm] = useState<CreateToolSet>({
        name: toolSet?.name || '',
        description: toolSet?.description || '',
        tools: toolSet?.tools || [],
        is_active: toolSet?.is_active ?? true,
    });
    const [toolsText, setToolsText] = useState((toolSet?.tools || []).join(', '));

    const createMutation = useMutation({
        mutationFn: (data: CreateToolSet) => toolSetsApi.create(data),
        onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['tool-sets'] }); toast.success('Tool set created'); onClose(); },
        onError: (err) => toast.error(err.message),
    });
    const updateMutation = useMutation({
        mutationFn: (data: CreateToolSet) => toolSetsApi.update(toolSet!.id, data),
        onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['tool-sets'] }); toast.success('Tool set updated'); onClose(); },
        onError: (err) => toast.error(err.message),
    });

    const handleSave = () => {
        const data = { ...form, tools: toolsText.split(',').map(t => t.trim()).filter(Boolean) };
        toolSet ? updateMutation.mutate(data) : createMutation.mutate(data);
    };

    return (
        <DialogContent>
            <DialogHeader><DialogTitle>{toolSet ? 'Edit Tool Set' : 'New Tool Set'}</DialogTitle></DialogHeader>
            <div className="space-y-4 py-4">
                <div>
                    <label className="text-sm text-muted-foreground">Name</label>
                    <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="standard" />
                </div>
                <div>
                    <label className="text-sm text-muted-foreground">Description</label>
                    <Input value={form.description || ''} onChange={(e) => setForm({ ...form, description: e.target.value })} />
                </div>
                <div>
                    <label className="text-sm text-muted-foreground">Tools (comma separated)</label>
                    <Textarea value={toolsText} onChange={(e) => setToolsText(e.target.value)} rows={4} placeholder="Read, Write, Shell, Glob, Grep" />
                </div>
            </div>
            <DialogFooter>
                <DialogClose asChild><Button variant="ghost">Cancel</Button></DialogClose>
                <Button onClick={handleSave} disabled={createMutation.isPending || updateMutation.isPending || !form.name}>Save</Button>
            </DialogFooter>
        </DialogContent>
    );
}

export default function ToolSetsPage() {
    const queryClient = useQueryClient();
    const [editingSet, setEditingSet] = useState<ToolSet | undefined>(undefined);
    const [showDialog, setShowDialog] = useState(false);
    const { data: toolSets, isLoading } = useQuery({
        queryKey: ['tool-sets'],
        queryFn: toolSetsApi.list,
    });

    const deleteMutation = useMutation({
        mutationFn: toolSetsApi.delete,
        onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['tool-sets'] }); toast.success('Tool set deleted'); },
        onError: (err) => toast.error(err.message),
    });

    if (isLoading) return <Card><CardContent className="p-6"><Skeleton className="h-64 w-full" /></CardContent></Card>;

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-end">
                <Dialog open={showDialog} onOpenChange={(o) => { setShowDialog(o); if (!o) setEditingSet(undefined); }}>
                    <DialogTrigger asChild>
                        <Button size="sm"><Plus className="h-4 w-4 mr-2" /> New Tool Set</Button>
                    </DialogTrigger>
                    <ToolSetFormDialog toolSet={editingSet} onClose={() => setShowDialog(false)} />
                </Dialog>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {toolSets?.map((ts) => (
                    <Card key={ts.id}>
                        <CardHeader className="pb-3">
                            <div className="flex items-center justify-between">
                                <CardTitle className="text-sm flex items-center gap-2">
                                    <Wrench className="h-4 w-4" /> {ts.name}
                                </CardTitle>
                                <div className="flex gap-1">
                                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => { setEditingSet(ts); setShowDialog(true); }}>
                                        <Pencil className="h-3 w-3" />
                                    </Button>
                                    <Dialog>
                                        <DialogTrigger asChild>
                                            <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive"><Trash2 className="h-3 w-3" /></Button>
                                        </DialogTrigger>
                                        <DialogContent>
                                            <DialogHeader><DialogTitle>Delete Tool Set</DialogTitle></DialogHeader>
                                            <p className="text-sm text-muted-foreground py-4">Are you sure you want to delete tool set for <strong>{ts.name}</strong>?</p>
                                            <DialogFooter>
                                                <DialogClose asChild><Button variant="ghost">Cancel</Button></DialogClose>
                                                <DialogClose asChild><Button variant="destructive" onClick={() => deleteMutation.mutate(ts.id)}>Delete</Button></DialogClose>
                                            </DialogFooter>
                                        </DialogContent>
                                    </Dialog>
                                </div>
                            </div>
                        </CardHeader>
                        <CardContent className="space-y-3">
                            {ts.description && <p className="text-xs text-muted-foreground">{ts.description}</p>}
                            <div className="flex flex-wrap gap-1">
                                {(!ts.tools || ts.tools.length === 0) ? (
                                    <Badge variant="outline" className="text-xs">∞ All tools</Badge>
                                ) : (
                                    ts.tools.map((t) => (
                                        <Badge key={t} variant="secondary" className="text-xs">{t}</Badge>
                                    ))
                                )}
                            </div>
                            <div className="flex items-center justify-between text-xs text-muted-foreground pt-2 border-t border-border">
                                <span>{ts.tools ? ts.tools.length : '∞'} tools</span>
                                <Badge variant={ts.is_active ? 'default' : 'secondary'} className="text-xs">
                                    {ts.is_active ? 'Active' : 'Inactive'}
                                </Badge>
                            </div>
                        </CardContent>
                    </Card>
                ))}
            </div>
        </div>
    );
}

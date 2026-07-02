'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
    systemPromptsApi, modelMappingsApi, modelGroupsApi, nodesApi, usersApi,
    type SystemPrompt, type SystemPromptScope,
} from '@/lib/api';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import {
    Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import {
    Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from '@/components/ui/dialog';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import {
    Command, CommandInput, CommandList, CommandEmpty, CommandGroup, CommandItem,
} from '@/components/ui/command';
import {
    DndContext, closestCenter, KeyboardSensor, PointerSensor, useSensor, useSensors,
    type DragEndEvent,
} from '@dnd-kit/core';
import {
    arrayMove, SortableContext, sortableKeyboardCoordinates, useSortable,
    verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { toast } from 'sonner';
import { useState, useMemo, useEffect } from 'react';
import { Plus, Trash2, Pencil, Check, ChevronsUpDown, GripVertical } from 'lucide-react';
import { cn } from '@/lib/utils';

const SCOPES: { value: SystemPromptScope; label: string; hint: string }[] = [
    { value: 'user', label: 'User', hint: 'username (top of the hierarchy)' },
    { value: 'mapping', label: 'Mapping', hint: 'display_name (a specific model)' },
    { value: 'model', label: 'Model', hint: 'real_name (underlying model)' },
    { value: 'group', label: 'Group', hint: 'group name' },
    { value: 'node', label: 'Node', hint: 'node name / code / id' },
];

/** Searchable combobox for scope_value: pick a known target or type a custom value. */
function ScopeValueCombobox({
    value, suggestions, onChange,
}: {
    value: string;
    suggestions: string[];
    onChange: (v: string) => void;
}) {
    const [open, setOpen] = useState(false);
    const [search, setSearch] = useState('');
    const typed = search.trim();
    const showCustom = typed.length > 0 && !suggestions.includes(typed);

    return (
        <Popover open={open} onOpenChange={setOpen}>
            <PopoverTrigger asChild>
                <button
                    type="button"
                    role="combobox"
                    aria-expanded={open}
                    className="flex h-9 w-full items-center justify-between rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm hover:bg-accent/40"
                >
                    <span className={value ? 'truncate' : 'text-muted-foreground'}>
                        {value || 'Select or type a target…'}
                    </span>
                    <ChevronsUpDown className="h-4 w-4 opacity-50 shrink-0" />
                </button>
            </PopoverTrigger>
            <PopoverContent className="w-[var(--radix-popover-trigger-width)] p-0" align="start">
                <Command>
                    <CommandInput placeholder="Search or type a custom value…" value={search} onValueChange={setSearch} />
                    <CommandList>
                        <CommandEmpty>No matches.</CommandEmpty>
                        {suggestions.length > 0 && (
                            <CommandGroup heading="Known targets">
                                {suggestions.map((s) => (
                                    <CommandItem key={s} value={s} onSelect={() => { onChange(s); setOpen(false); }}>
                                        <Check className={cn('mr-2 h-4 w-4', value === s ? 'opacity-100' : 'opacity-0')} />
                                        {s}
                                    </CommandItem>
                                ))}
                            </CommandGroup>
                        )}
                        {showCustom && (
                            <CommandGroup heading="Custom">
                                <CommandItem value={typed} onSelect={() => { onChange(typed); setOpen(false); }}>
                                    <Check className={cn('mr-2 h-4 w-4', value === typed ? 'opacity-100' : 'opacity-0')} />
                                    Use “{typed}”
                                </CommandItem>
                            </CommandGroup>
                        )}
                    </CommandList>
                </Command>
            </PopoverContent>
        </Popover>
    );
}

function scopeBadge(scope: SystemPromptScope) {
    const map: Record<SystemPromptScope, string> = {
        user: 'text-rose-400 border-rose-400/30 bg-rose-400/10',
        mapping: 'text-blue-400 border-blue-400/30 bg-blue-400/10',
        model: 'text-emerald-400 border-emerald-400/30 bg-emerald-400/10',
        group: 'text-violet-400 border-violet-400/30 bg-violet-400/10',
        node: 'text-orange-400 border-orange-400/30 bg-orange-400/10',
    };
    return <Badge variant="outline" className={`${map[scope]} text-xs`}>{scope}</Badge>;
}

function SortablePromptRow({
    prompt, draggable, onToggle, onEdit, onDelete,
}: {
    prompt: SystemPrompt;
    draggable: boolean;
    onToggle: (p: SystemPrompt) => void;
    onEdit: (p: SystemPrompt) => void;
    onDelete: (id: number) => void;
}) {
    const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: prompt.id });
    const style = { transform: CSS.Transform.toString(transform), transition };
    return (
        <div
            ref={setNodeRef}
            style={style}
            className={cn(
                'flex items-center gap-3 px-4 py-2 border-t border-border/40',
                isDragging && 'opacity-70 bg-accent/40 relative z-10',
            )}
        >
            {draggable ? (
                <button
                    {...attributes}
                    {...listeners}
                    className="cursor-grab touch-none text-muted-foreground hover:text-foreground shrink-0"
                    aria-label="Drag to reorder"
                >
                    <GripVertical className="h-4 w-4" />
                </button>
            ) : (
                <span className="w-4 shrink-0" />
            )}
            <div className="flex-1 min-w-0">
                <p className="text-xs text-muted-foreground line-clamp-2" title={prompt.prompt}>{prompt.prompt}</p>
                {prompt.description && (
                    <p className="text-[11px] text-muted-foreground/70 italic mt-0.5">{prompt.description}</p>
                )}
            </div>
            <Badge variant="outline" className="text-[10px] tabular-nums shrink-0">prio {prompt.priority}</Badge>
            <Switch checked={prompt.is_active} onCheckedChange={() => onToggle(prompt)} />
            <div className="flex items-center gap-1 shrink-0">
                <Button size="icon" variant="ghost" className="h-8 w-8" onClick={() => onEdit(prompt)}>
                    <Pencil className="h-3.5 w-3.5" />
                </Button>
                <Button
                    size="icon" variant="ghost" className="h-8 w-8 text-destructive"
                    onClick={() => onDelete(prompt.id)}
                >
                    <Trash2 className="h-3.5 w-3.5" />
                </Button>
            </div>
        </div>
    );
}

/** One (scope_type, scope_value) target: its prompts stack top-first; drag to reorder. */
function PromptTargetGroup({
    prompts, onReorder, onToggle, onEdit, onDelete,
}: {
    prompts: SystemPrompt[];
    onReorder: (ids: number[]) => void;
    onToggle: (p: SystemPrompt) => void;
    onEdit: (p: SystemPrompt) => void;
    onDelete: (id: number) => void;
}) {
    const [order, setOrder] = useState<number[]>(() => prompts.map((p) => p.id));
    useEffect(() => { setOrder(prompts.map((p) => p.id)); }, [prompts]);

    const sensors = useSensors(
        useSensor(PointerSensor),
        useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
    );

    const byId = new Map(prompts.map((p) => [p.id, p]));
    const ordered = order.map((id) => byId.get(id)).filter(Boolean) as SystemPrompt[];
    const first = prompts[0];

    const onDragEnd = (e: DragEndEvent) => {
        const { active, over } = e;
        if (over && active.id !== over.id) {
            const next = arrayMove(order, order.indexOf(Number(active.id)), order.indexOf(Number(over.id)));
            setOrder(next);
            onReorder(next);
        }
    };

    return (
        <div className="border-b border-border/60 last:border-0 pb-2">
            <div className="flex items-center gap-2 px-4 pt-3 pb-1">
                {scopeBadge(first.scope_type)}
                <span className="font-mono text-xs truncate">{first.scope_value}</span>
                {prompts.length > 1 && (
                    <span className="text-[11px] text-muted-foreground/70 ml-auto shrink-0">
                        drag to reorder — top is injected first
                    </span>
                )}
            </div>
            <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
                <SortableContext items={order} strategy={verticalListSortingStrategy}>
                    {ordered.map((p) => (
                        <SortablePromptRow
                            key={p.id}
                            prompt={p}
                            draggable={prompts.length > 1}
                            onToggle={onToggle}
                            onEdit={onEdit}
                            onDelete={onDelete}
                        />
                    ))}
                </SortableContext>
            </DndContext>
        </div>
    );
}

interface FormState {
    id?: number;
    scope_type: SystemPromptScope;
    scope_value: string;
    prompt: string;
    priority: number;
    description: string;
    is_active: boolean;
}

const EMPTY_FORM: FormState = {
    scope_type: 'mapping', scope_value: '', prompt: '', priority: 0, description: '', is_active: true,
};

export default function SystemPromptsPage() {
    const qc = useQueryClient();
    const [dialogOpen, setDialogOpen] = useState(false);
    const [form, setForm] = useState<FormState>(EMPTY_FORM);

    const { data: prompts, isLoading } = useQuery({
        queryKey: ['system-prompts'],
        queryFn: systemPromptsApi.list,
    });

    // Suggestion sources for the scope_value field (free text still allowed).
    const { data: mappings } = useQuery({ queryKey: ['model-mappings'], queryFn: modelMappingsApi.list });
    const { data: groups } = useQuery({ queryKey: ['model-groups'], queryFn: modelGroupsApi.list });
    const { data: nodes } = useQuery({ queryKey: ['nodes'], queryFn: () => nodesApi.list() });
    const { data: users } = useQuery({ queryKey: ['users'], queryFn: usersApi.list });

    const suggestions = useMemo<string[]>(() => {
        switch (form.scope_type) {
            case 'user':
                return (users ?? []).map((u) => u.username);
            case 'mapping':
                return (mappings ?? []).map((m) => m.display_name);
            case 'model':
                return Array.from(new Set((mappings ?? []).map((m) => m.real_name)));
            case 'group':
                return (groups?.groups ?? []).map((g) => g.name);
            case 'node':
                return (nodes ?? []).flatMap((n) => [n.name, n.code].filter(Boolean) as string[]);
            default:
                return [];
        }
    }, [form.scope_type, mappings, groups, nodes, users]);

    const invalidate = () => qc.invalidateQueries({ queryKey: ['system-prompts'] });

    const saveMutation = useMutation({
        mutationFn: (f: FormState) => {
            const body = {
                scope_type: f.scope_type,
                scope_value: f.scope_value.trim(),
                prompt: f.prompt,
                priority: f.priority,
                is_active: f.is_active,
                description: f.description.trim() || null,
            };
            return f.id ? systemPromptsApi.update(f.id, body) : systemPromptsApi.create(body);
        },
        onSuccess: () => {
            invalidate();
            setDialogOpen(false);
            toast.success('System prompt saved');
        },
        onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Save failed'),
    });

    const toggleMutation = useMutation({
        mutationFn: (p: SystemPrompt) => systemPromptsApi.update(p.id, { is_active: !p.is_active }),
        onSuccess: invalidate,
        onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Update failed'),
    });

    const deleteMutation = useMutation({
        mutationFn: (id: number) => systemPromptsApi.delete(id),
        onSuccess: () => { invalidate(); toast.success('Deleted'); },
        onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Delete failed'),
    });

    const reorderMutation = useMutation({
        mutationFn: (ids: number[]) => systemPromptsApi.reorder(ids),
        onSuccess: () => { invalidate(); toast.success('Order saved'); },
        onError: (e: unknown) => {
            invalidate(); // roll back optimistic local order
            toast.error(e instanceof Error ? e.message : 'Reorder failed');
        },
    });

    // Group contiguously by (scope_type, scope_value) — server returns them grouped.
    const groupedPrompts = useMemo(() => {
        const groups: { key: string; items: SystemPrompt[] }[] = [];
        for (const p of prompts ?? []) {
            const key = `${p.scope_type} ${p.scope_value}`;
            const last = groups[groups.length - 1];
            if (last && last.key === key) last.items.push(p);
            else groups.push({ key, items: [p] });
        }
        return groups;
    }, [prompts]);

    const openCreate = () => { setForm(EMPTY_FORM); setDialogOpen(true); };
    const openEdit = (p: SystemPrompt) => {
        setForm({
            id: p.id, scope_type: p.scope_type, scope_value: p.scope_value,
            prompt: p.prompt, priority: p.priority, description: p.description ?? '', is_active: p.is_active,
        });
        setDialogOpen(true);
    };

    const canSave = form.scope_value.trim().length > 0 && form.prompt.trim().length > 0;

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between">
                <p className="text-sm text-muted-foreground">
                    System prompts injected automatically and transparently into every matching request. All
                    scopes matching a request (mapping/model/group/node) are combined in priority order.
                </p>
                <Button size="sm" onClick={openCreate}>
                    <Plus className="h-4 w-4 mr-1" /> New Prompt
                </Button>
            </div>

            <Card>
                <CardContent className="p-0 overflow-x-auto">
                    {isLoading ? (
                        <div className="p-4 space-y-2">
                            {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
                        </div>
                    ) : !prompts || prompts.length === 0 ? (
                        <p className="text-sm text-muted-foreground text-center py-12">
                            No system prompts yet. Add one with &quot;New Prompt&quot;.
                        </p>
                    ) : (
                        <div>
                            {groupedPrompts.map((g) => (
                                <PromptTargetGroup
                                    key={g.key}
                                    prompts={g.items}
                                    onReorder={(ids) => reorderMutation.mutate(ids)}
                                    onToggle={(p) => toggleMutation.mutate(p)}
                                    onEdit={openEdit}
                                    onDelete={(id) => { if (confirm('Delete this system prompt?')) deleteMutation.mutate(id); }}
                                />
                            ))}
                        </div>
                    )}
                </CardContent>
            </Card>

            <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                <DialogContent className="max-w-lg">
                    <DialogHeader>
                        <DialogTitle>{form.id ? 'Edit System Prompt' : 'New System Prompt'}</DialogTitle>
                    </DialogHeader>
                    <div className="space-y-4">
                        <div className="grid grid-cols-2 gap-3">
                            <div className="space-y-1">
                                <label className="text-xs text-muted-foreground">Scope Type</label>
                                <Select
                                    value={form.scope_type}
                                    onValueChange={(v) => setForm((f) => ({ ...f, scope_type: v as SystemPromptScope, scope_value: '' }))}
                                >
                                    <SelectTrigger><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        {SCOPES.map((s) => (
                                            <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="space-y-1">
                                <label className="text-xs text-muted-foreground">Priority</label>
                                <Input
                                    type="number"
                                    value={form.priority}
                                    onChange={(e) => setForm((f) => ({ ...f, priority: Number(e.target.value) || 0 }))}
                                />
                            </div>
                        </div>

                        <div className="space-y-1">
                            <label className="text-xs text-muted-foreground">
                                Target — {SCOPES.find((s) => s.value === form.scope_type)?.hint}
                            </label>
                            <ScopeValueCombobox
                                value={form.scope_value}
                                suggestions={suggestions}
                                onChange={(v) => setForm((f) => ({ ...f, scope_value: v }))}
                            />
                        </div>

                        <div className="space-y-1">
                            <label className="text-xs text-muted-foreground">System Prompt</label>
                            <Textarea
                                rows={6}
                                value={form.prompt}
                                placeholder="System prompt text injected into requests…"
                                onChange={(e) => setForm((f) => ({ ...f, prompt: e.target.value }))}
                            />
                        </div>

                        <div className="space-y-1">
                            <label className="text-xs text-muted-foreground">Description (optional)</label>
                            <Input
                                value={form.description}
                                placeholder="admin note"
                                onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                            />
                        </div>

                        <div className="flex items-center gap-2">
                            <Switch checked={form.is_active} onCheckedChange={(v) => setForm((f) => ({ ...f, is_active: v }))} />
                            <span className="text-sm">Active</span>
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="ghost" onClick={() => setDialogOpen(false)}>Cancel</Button>
                        <Button disabled={!canSave || saveMutation.isPending} onClick={() => saveMutation.mutate(form)}>
                            {saveMutation.isPending ? 'Saving…' : 'Save'}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}

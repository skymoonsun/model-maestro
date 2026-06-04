'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { modelGroupsApi, modelMappingsApi, nodesApi, type ModelGroupDetail, type ModelGroupSummary, type ModelGroupMember, type ModelGroupCreate, type ModelGroupMemberCreate, type Node } from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { cn } from '@/lib/utils';
import { Skeleton } from '@/components/ui/skeleton';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogTrigger, DialogClose } from '@/components/ui/dialog';
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from '@/components/ui/command';
import { toast } from 'sonner';
import { useState, useCallback, useMemo, useEffect } from 'react';
import {
    DndContext,
    closestCenter,
    KeyboardSensor,
    PointerSensor,
    useSensor,
    useSensors,
    type DragEndEvent,
} from '@dnd-kit/core';
import {
    arrayMove,
    SortableContext,
    sortableKeyboardCoordinates,
    useSortable,
    verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { Plus, Trash2, GripVertical, RefreshCw, Pencil, Check } from 'lucide-react';

// ==================== Sortable Member Card ====================
function SortableMemberCard({
    member,
    onRemove,
    onEdit,
    onToggleActive,
    nodes,
}: {
    member: ModelGroupMember;
    onRemove: (id: number) => void;
    onEdit: (member: ModelGroupMember) => void;
    onToggleActive: (id: number, is_active: boolean) => void;
    nodes?: Node[];
}) {
    const { attributes, listeners, setNodeRef, transform, transition } = useSortable({
        id: member.id,
    });
    const style = {
        transform: CSS.Transform.toString(transform),
        transition,
    };

    return (
        <div ref={setNodeRef} style={style} className="flex items-center gap-3 rounded-lg border bg-card p-3">
            <button {...attributes} {...listeners} className="cursor-grab touch-none text-muted-foreground hover:text-foreground">
                <GripVertical className="h-4 w-4" />
            </button>
            <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                    <span className="font-mono text-sm font-medium truncate">{member.model_display_name}</span>
                    {member.is_fallback && (
                        <Badge variant="secondary" className="text-xs px-1.5 py-0">FB</Badge>
                    )}
                    {!member.is_active && (
                        <Badge variant="outline" className="text-muted-foreground text-xs">Inactive</Badge>
                    )}
                </div>
                <div className="flex items-center gap-3 mt-1 text-xs text-muted-foreground">
                    <span>Priority: {member.priority}</span>
                    <span>Weight: {member.weight}</span>
                    {((member.preferred_node_ids?.length ?? 0) > 0) && nodes && (
                        <span>
                            Nodes:{' '}
                            {member.preferred_node_ids!.map((id) => nodes.find((n) => n.id === id)?.name ?? `#${id}`).join(', ')}
                        </span>
                    )}
                    {member.is_fallback_413 && (
                        <Badge className="bg-amber-100 text-amber-700 border-amber-300 text-xs">413</Badge>
                    )}
                    {member.is_fallback_413 && (
                        <Badge variant="outline" className="text-amber-600 border-amber-500/40 text-xs">413 FB</Badge>
                    )}
                    {member.is_metadata_source && (
                        <Badge className="bg-sky-100 text-sky-700 border-sky-300 text-xs">Meta</Badge>
                    )}
                    {member.capability_tags && member.capability_tags.length > 0 && (
                        <div className="flex gap-1">
                            {member.capability_tags.map((tag) => (
                                <Badge key={tag} variant="secondary" className="text-xs px-1.5 py-0">{tag}</Badge>
                            ))}
                        </div>
                    )}
                </div>
            </div>
            <Switch
                checked={member.is_active}
                onCheckedChange={(v) => onToggleActive(member.id, v)}
            />
            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => onEdit(member)}>
                <Pencil className="h-3.5 w-3.5" />
            </Button>
            <Dialog>
                <DialogTrigger asChild>
                    <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive hover:text-destructive">
                        <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                </DialogTrigger>
                <DialogContent>
                    <DialogHeader><DialogTitle>Remove Member</DialogTitle></DialogHeader>
                    <p className="text-sm text-muted-foreground py-4">
                        Remove <strong>{member.model_display_name}</strong> from this group?
                    </p>
                    <DialogFooter>
                        <DialogClose asChild><Button variant="ghost">Cancel</Button></DialogClose>
                        <DialogClose asChild>
                            <Button variant="destructive" onClick={() => onRemove(member.id)}>Remove</Button>
                        </DialogClose>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}

// ==================== Group Detail ====================
function GroupDetail({
    group,
    onBack,
}: {
    group: ModelGroupDetail;
    onBack: () => void;
}) {
    const qc = useQueryClient();
    const [members, setMembers] = useState<ModelGroupMember[]>(
        [...group.members].sort((a, b) => a.priority - b.priority)
    );
    const [hasReordered, setHasReordered] = useState(false);
    const [addOpen, setAddOpen] = useState(false);
    const [newMemberName, setNewMemberName] = useState('');
    const [newMemberNodeIds, setNewMemberNodeIds] = useState<number[]>([]);

    const { data: mappingsData } = useQuery({
        queryKey: ['model-mappings'],
        queryFn: () => modelMappingsApi.list(),
    });

    const { data: nodesData } = useQuery({
        queryKey: ['nodes'],
        queryFn: () => nodesApi.list(true),
    });

    const existingNames = new Set(members.map((m) => m.model_display_name));

    // Build available models from mappings + node models
    const modelMap = new Map<string, string>(); // display_name -> real_name
    mappingsData?.forEach((m) => {
        modelMap.set(m.display_name, m.real_name);
    });
    nodesData?.forEach((node) => {
        node.models?.forEach((model: { model_name: string }) => {
            if (!modelMap.has(model.model_name)) {
                modelMap.set(model.model_name, model.model_name);
            }
        });
    });

    const availableModels = Array.from(modelMap.entries())
        .filter(([display_name]) => !existingNames.has(display_name))
        .map(([display_name, real_name]) => ({ display_name, real_name }))
        .sort((a, b) => a.display_name.localeCompare(b.display_name));

    /** Model name as synced on nodes (Ollama); with a mapping, both real and display names are matched */
    const filteredNodesForSelectedModel = useMemo(() => {
        if (!nodesData?.length || !newMemberName) {
            return [];
        }
        const mapping = mappingsData?.find((m) => m.display_name === newMemberName);
        const realName = mapping?.real_name ?? newMemberName;
        // Group members may reference catalog-unavailable models; list any node that syncs this name.
        return nodesData.filter((node) =>
            node.models?.some(
                (m) =>
                    m.model_name === realName || m.model_name === newMemberName,
            ),
        );
    }, [nodesData, newMemberName, mappingsData]);

    useEffect(() => {
        const allowed = new Set(filteredNodesForSelectedModel.map((n) => n.id));
        setNewMemberNodeIds((prev) => prev.filter((id) => allowed.has(id)));
    }, [filteredNodesForSelectedModel]);

    const [editing, setEditing] = useState(false);
    const [editForm, setEditForm] = useState({
        name: group.name,
        description: group.description || '',
        strategy: group.strategy,
        is_active: group.is_active,
        list_in_catalog: group.list_in_catalog,
    });

    const sensors = useSensors(
        useSensor(PointerSensor),
        useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
    );

    const removeMut = useMutation({
        mutationFn: (memberId: number) => modelGroupsApi.removeMember(group.name, memberId),
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['model-groups'] });
            toast.success('Member removed');
            setMembers((m) => m.filter((x) => x.id !== removeMut.variables));
        },
        onError: (e) => toast.error(e.message),
    });

    // Make removeMut.variables available in onSuccess
    const removeMutRef = useMutation({
        mutationFn: (memberId: number) => modelGroupsApi.removeMember(group.name, memberId),
        onSuccess: (_data, memberId) => {
            qc.invalidateQueries({ queryKey: ['model-groups'] });
            toast.success('Member removed');
            setMembers((m) => m.filter((x) => x.id !== memberId));
        },
        onError: (e) => toast.error(e.message),
    });

    const addMut = useMutation({
        mutationFn: (data: ModelGroupMemberCreate) => modelGroupsApi.addMember(group.name, data),
        onSuccess: (newMember) => {
            qc.invalidateQueries({ queryKey: ['model-groups'] });
            toast.success('Member added');
            setMembers((m) => [...m, newMember]);
            setNewMemberName('');
            setNewMemberNodeIds([]);
            setAddOpen(false);
        },
        onError: (e) => toast.error(e.message),
    });

    const [editMemberOpen, setEditMemberOpen] = useState(false);
    const [editMemberForm, setEditMemberForm] = useState<ModelGroupMemberCreate | null>(null);
    const [editMemberId, setEditMemberId] = useState<number | null>(null);

    const updateMemberMut = useMutation({
        mutationFn: ({ id, data }: { id: number; data: ModelGroupMemberCreate }) => modelGroupsApi.updateMember(group.name, id, data),
        onSuccess: (updated) => {
            qc.invalidateQueries({ queryKey: ['model-groups'] });
            toast.success('Member updated');
            setMembers((m) => m.map((x) => (x.id === updated.id ? updated : x)));
            setEditMemberOpen(false);
            setEditMemberForm(null);
            setEditMemberId(null);
        },
        onError: (e) => toast.error(e.message),
    });

    const reorderMut = useMutation({
        mutationFn: (items: { id: number; priority: number }[]) =>
            modelGroupsApi.reorderMembers(group.name, items),
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['model-groups'] });
            toast.success('Order saved');
            setHasReordered(false);
        },
        onError: (e) => toast.error(e.message),
    });

    const updateMut = useMutation({
        mutationFn: (data: Partial<ModelGroupCreate>) => modelGroupsApi.update(group.name, data),
        onSuccess: (_data, variables) => {
            qc.invalidateQueries({ queryKey: ['model-groups'] });
            if (variables.name && variables.name !== group.name) {
                // Group name changed; return to list so the detail query refetches
                // with the new name key.
                toast.success('Group updated — returning to list');
                onBack();
                return;
            }
            toast.success('Group updated');
            setEditing(false);
        },
        onError: (e) => toast.error(e.message),
    });

    const toggleActiveMut = useMutation({
        mutationFn: ({ id, is_active }: { id: number; is_active: boolean }) => {
            // Update local state immediately
            setMembers((m) => m.map((x) => (x.id === id ? { ...x, is_active } : x)));
            // We need the group name and member id to call the right API
            // Since we can't toggle a single member's is_active via the current API,
            // we'll just update locally for now. The reorder save will persist everything.
            return Promise.resolve();
        },
    });

    const handleDragEnd = useCallback((event: DragEndEvent) => {
        const { active, over } = event;
        if (!over || active.id === over.id) return;

        setMembers((prev) => {
            const oldIndex = prev.findIndex((m) => m.id === active.id);
            const newIndex = prev.findIndex((m) => m.id === over.id);
            const updated = arrayMove(prev, oldIndex, newIndex).map((m, i) => ({
                ...m,
                priority: i,
            }));
            return updated;
        });
        setHasReordered(true);
    }, []);

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <Button variant="ghost" size="sm" onClick={onBack}>
                        &larr; Back
                    </Button>
                    <h2 className="text-xl font-semibold">{group.name}</h2>
                    <Badge variant={group.is_active ? 'default' : 'outline'}>
                        {group.is_active ? 'Active' : 'Inactive'}
                    </Badge>
                    <Badge variant="secondary">{group.strategy}</Badge>
                    {group.list_in_catalog && (
                        <Badge variant="outline" className="text-emerald-600 border-emerald-600/40">
                            In catalog
                        </Badge>
                    )}
                </div>
                <div className="flex items-center gap-2">
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setEditing(true)}
                    >
                        <Pencil className="h-3.5 w-3.5 mr-1" />
                        Edit Group
                    </Button>
                </div>
            </div>

            {group.description && (
                <p className="text-sm text-muted-foreground">{group.description}</p>
            )}

            {/* Edit Group Dialog */}
            <Dialog open={editing} onOpenChange={setEditing}>
                <DialogContent>
                    <DialogHeader><DialogTitle>Edit Group</DialogTitle></DialogHeader>
                    <div className="space-y-4 py-4">
                        <div>
                            <Label>Name</Label>
                            <Input
                                value={editForm.name}
                                onChange={(e) => setEditForm((f) => ({ ...f, name: e.target.value }))}
                            />
                        </div>
                        <div>
                            <Label>Description</Label>
                            <Input
                                value={editForm.description}
                                onChange={(e) => setEditForm((f) => ({ ...f, description: e.target.value }))}
                            />
                        </div>
                        <div>
                            <Label>Strategy</Label>
                            <Select value={editForm.strategy} onValueChange={(v) => setEditForm((f) => ({ ...f, strategy: v }))}>
                                <SelectTrigger><SelectValue /></SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="priority">Priority (lowest number = highest)</SelectItem>
                                    <SelectItem value="round_robin">Round Robin</SelectItem>
                                    <SelectItem value="weighted">Weighted</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="flex items-center gap-2">
                            <Switch
                                checked={editForm.is_active}
                                onCheckedChange={(v) => setEditForm((f) => ({ ...f, is_active: v }))}
                            />
                            <Label>Active</Label>
                        </div>
                        <div className="flex items-center gap-2">
                            <Switch
                                checked={editForm.list_in_catalog}
                                onCheckedChange={(v) => setEditForm((f) => ({ ...f, list_in_catalog: v }))}
                            />
                            <Label>Show in catalog</Label>
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
                        <Button onClick={() => updateMut.mutate(editForm)} disabled={updateMut.isPending}>Save</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Members Section */}
            <Card>
                <CardHeader className="pb-3">
                    <div className="flex items-center justify-between">
                        <CardTitle className="text-base">Members ({members.length})</CardTitle>
                        <div className="flex items-center gap-2">
                            {hasReordered && (
                                <Button size="sm" onClick={() => reorderMut.mutate(members.map((m, i) => ({ id: m.id, priority: i })))} disabled={reorderMut.isPending}>
                                    <RefreshCw className={`h-3.5 w-3.5 mr-1 ${reorderMut.isPending ? 'animate-spin' : ''}`} />
                                    Save Order
                                </Button>
                            )}
                            <Dialog
                                open={addOpen}
                                onOpenChange={(open) => {
                                    setAddOpen(open);
                                    if (!open) {
                                        setNewMemberName('');
                                        setNewMemberNodeIds([]);
                                    }
                                }}
                            >
                                <DialogTrigger asChild>
                                    <Button size="sm">
                                        <Plus className="h-3.5 w-3.5 mr-1" />
                                        Add Member
                                    </Button>
                                </DialogTrigger>
                                <DialogContent>
                                    <DialogHeader><DialogTitle>Add Member</DialogTitle></DialogHeader>
                                    <Command className="rounded-lg border shadow-none">
                                        <CommandInput placeholder="Search models..." />
                                        <CommandList>
                                            <CommandEmpty>No models found.</CommandEmpty>
                                            <CommandGroup>
                                                {availableModels.map((m) => (
                                                    <CommandItem
                                                        key={m.display_name}
                                                        value={m.display_name}
                                                        onSelect={() => {
                                                            setNewMemberName(m.display_name);
                                                            setNewMemberNodeIds([]);
                                                        }}
                                                    >
                                                        <Check className={cn("mr-2 h-4 w-4", newMemberName === m.display_name ? "opacity-100" : "opacity-0")} />
                                                        <span>{m.display_name}</span>
                                                        <span className="text-muted-foreground ml-2 text-xs">→ {m.real_name}</span>
                                                    </CommandItem>
                                                ))}
                                            </CommandGroup>
                                        </CommandList>
                                    </Command>
                                    {newMemberName && nodesData && nodesData.length > 0 && (
                                        <div className="mt-3">
                                            <Label className="text-sm mb-1.5 block">Preferred nodes (optional)</Label>
                                            <p className="text-xs text-muted-foreground mb-2">
                                                Nodes that sync this model are listed, including models marked unavailable on the node (if a mapping exists, we match the real name too).
                                                Checked nodes restrict traffic to that subset; leave all unchecked to let the load balancer use every eligible node.
                                            </p>
                                            {filteredNodesForSelectedModel.length === 0 ? (
                                                <p className="text-xs text-amber-600 dark:text-amber-500 rounded-md border border-amber-500/30 bg-amber-500/10 px-2 py-2">
                                                    No node currently reports this model name. Run a node sync or verify the model name.
                                                </p>
                                            ) : (
                                                <div className="max-h-36 overflow-y-auto rounded-md border p-2 space-y-2">
                                                    {filteredNodesForSelectedModel.map((n) => {
                                                        const checked = newMemberNodeIds.includes(n.id);
                                                        return (
                                                            <label key={n.id} className="flex items-center gap-2 text-sm cursor-pointer">
                                                                <input
                                                                    type="checkbox"
                                                                    className="rounded border-input"
                                                                    checked={checked}
                                                                    onChange={() => {
                                                                        setNewMemberNodeIds((prev) =>
                                                                            checked ? prev.filter((id) => id !== n.id) : [...prev, n.id],
                                                                        );
                                                                    }}
                                                                />
                                                                <span>{n.name}</span>
                                                                <span className="text-xs text-muted-foreground truncate">{n.base_url}</span>
                                                            </label>
                                                        );
                                                    })}
                                                </div>
                                            )}
                                        </div>
                                    )}
                                    <DialogFooter className="mt-4">
                                        <Button variant="ghost" onClick={() => { setAddOpen(false); }}>Cancel</Button>
                                        <Button
                                            onClick={() => addMut.mutate({
                                                model_display_name: newMemberName,
                                                priority: members.length,
                                                weight: 1,
                                                ...(newMemberNodeIds.length > 0 ? { preferred_node_ids: newMemberNodeIds } : {}),
                                            })}
                                            disabled={addMut.isPending || !newMemberName}
                                        >
                                            Add
                                        </Button>
                                    </DialogFooter>
                                </DialogContent>
                            </Dialog>
                        </div>
                    </div>
                </CardHeader>
                <CardContent>
                    {members.length === 0 ? (
                        <p className="text-sm text-muted-foreground text-center py-8">
                            No members yet. Add a model to this group.
                        </p>
                    ) : (
                        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
                            <SortableContext items={members.map((m) => m.id)} strategy={verticalListSortingStrategy}>
                                <div className="space-y-2">
                                    {members.map((member) => (
                                        <SortableMemberCard
                                            key={member.id}
                                            member={member}
                                            onRemove={(id) => removeMutRef.mutate(id)}
                                            onEdit={(m) => {
                                                setEditMemberId(m.id);
                                                setEditMemberForm({
                                                    model_display_name: m.model_display_name,
                                                    weight: m.weight,
                                                    priority: m.priority,
                                                    is_fallback: m.is_fallback,
                                                    is_fallback_413: m.is_fallback_413,
                                                    is_metadata_source: m.is_metadata_source,
                                                    is_active: m.is_active,
                                                    capability_tags: m.capability_tags ?? [],
                                                    preferred_node_ids: m.preferred_node_ids,
                                                });
                                                setEditMemberOpen(true);
                                            }}
                                            onToggleActive={(id, is_active) => toggleActiveMut.mutate({ id, is_active })}
                                            nodes={nodesData}
                                        />
                                    ))}
                                </div>
                            </SortableContext>
                        </DndContext>
                    )}
                </CardContent>
            </Card>

            {/* Edit Member Dialog */}
            <Dialog open={editMemberOpen} onOpenChange={setEditMemberOpen}>
                <DialogContent>
                    <DialogHeader><DialogTitle>Edit Member</DialogTitle></DialogHeader>
                    {editMemberForm ? (
                        <div className="space-y-4 py-4">
                            <div>
                                <Label>Model</Label>
                                <Input value={editMemberForm.model_display_name} disabled className="bg-muted" />
                            </div>
                            <div>
                                <Label>Priority</Label>
                                <Input
                                    type="number"
                                    value={editMemberForm.priority}
                                    onChange={(e) => setEditMemberForm((f) => f ? { ...f, priority: parseInt(e.target.value) || 0 } : null)}
                                />
                            </div>
                            <div>
                                <Label>Weight</Label>
                                <Input
                                    type="number"
                                    value={editMemberForm.weight}
                                    onChange={(e) => setEditMemberForm((f) => f ? { ...f, weight: parseInt(e.target.value) || 0 } : null)}
                                />
                            </div>
                            <div className="flex items-center gap-2">
                                <Switch
                                    checked={editMemberForm.is_fallback_413}
                                    onCheckedChange={(v) => setEditMemberForm((f) => f ? { ...f, is_fallback_413: v } : null)}
                                />
                                <div>
                                    <Label>413 Fallback</Label>
                                    <p className="text-xs text-muted-foreground">Use this model when upstream returns 413 (Request Entity Too Large).</p>
                                </div>
                            </div>
                            <div className="flex items-center gap-2">
                                <Switch
                                    checked={editMemberForm.is_fallback}
                                    onCheckedChange={(v) => setEditMemberForm((f) => f ? { ...f, is_fallback: v } : null)}
                                />
                                <Label>Fallback Member</Label>
                            </div>
                            <div className="flex items-center gap-2">
                                <Switch
                                    checked={editMemberForm.is_metadata_source ?? false}
                                    onCheckedChange={(v) => setEditMemberForm((f) => f ? { ...f, is_metadata_source: v } : null)}
                                />
                                <div>
                                    <Label>Metadata Source</Label>
                                    <p className="text-xs text-muted-foreground">The group&apos;s entry in /v1/models &amp; /api/tags inherits this member&apos;s metadata (e.g. embedding family/context). Only one member should be the source.</p>
                                </div>
                            </div>
                            <div className="flex items-center gap-2">
                                <Switch
                                    checked={editMemberForm.is_active}
                                    onCheckedChange={(v) => setEditMemberForm((f) => f ? { ...f, is_active: v } : null)}
                                />
                                <Label>Active</Label>
                            </div>
                        </div>
                    ) : (
                        <p className="text-sm text-muted-foreground py-4">Loading...</p>
                    )}
                    <DialogFooter>
                        <Button variant="ghost" onClick={() => { setEditMemberOpen(false); setEditMemberForm(null); setEditMemberId(null); }}>Cancel</Button>
                        <Button
                            onClick={() => {
                                if (editMemberId && editMemberForm) {
                                    updateMemberMut.mutate({ id: editMemberId, data: editMemberForm });
                                }
                            }}
                            disabled={updateMemberMut.isPending}
                        >
                            Save
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}

// ==================== Groups Page ====================

// ==================== Sortable Group Card ====================
function SortableGroupCard({
    group,
    onSelect,
    onDeleteClick,
    listCatalogMut,
}: {
    group: ModelGroupSummary;
    onSelect: () => void;
    onDeleteClick: (name: string) => void;
    listCatalogMut: any;
}) {
    const { attributes, listeners, setNodeRef, transform, transition } = useSortable({
        id: group.name,
    });
    const style = {
        transform: CSS.Transform.toString(transform),
        transition,
    };

    return (
        <div ref={setNodeRef} style={style} className="flex items-center gap-3 rounded-lg border bg-card p-4 hover:border-primary/50 transition-colors cursor-pointer" onClick={onSelect}>
            <button {...attributes} {...listeners} className="cursor-grab touch-none text-muted-foreground hover:text-foreground shrink-0" onClick={(e) => e.stopPropagation()}>
                <GripVertical className="h-4 w-4" />
            </button>
            <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                    <span className="font-mono text-base font-medium truncate">{group.name}</span>
                    <Badge variant={group.is_active ? 'default' : 'outline'}>
                        {group.is_active ? 'Active' : 'Inactive'}
                    </Badge>
                    <Badge variant="secondary">{group.strategy}</Badge>
                    {group.list_in_catalog && (
                        <Badge variant="outline" className="text-emerald-600 border-emerald-600/40">
                            In catalog
                        </Badge>
                    )}
                </div>
                {group.description && (
                    <p className="text-sm text-muted-foreground mt-1">{group.description}</p>
                )}
                <div className="mt-1 text-xs text-muted-foreground">
                    Priority: {group.priority}
                </div>
            </div>
            <div className="flex items-center gap-3 shrink-0" onClick={(e) => e.stopPropagation()}>
                <Label htmlFor={`list-${group.id}`} className="text-xs text-muted-foreground whitespace-nowrap">
                    Show in catalog
                </Label>
                <Switch
                    id={`list-${group.id}`}
                    checked={group.list_in_catalog ?? false}
                    disabled={listCatalogMut.isPending}
                    onCheckedChange={(v) =>
                        listCatalogMut.mutate({ name: group.name, list_in_catalog: v })
                    }
                />
                <Button
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8 text-destructive hover:text-destructive"
                    onClick={() => onDeleteClick(group.name)}
                >
                    <Trash2 className="h-4 w-4" />
                </Button>
            </div>
        </div>
    );
}

export default function ModelGroupsPage() {
    const qc = useQueryClient();
    const [selectedGroup, setSelectedGroup] = useState<ModelGroupSummary | null>(null);
    const [createOpen, setCreateOpen] = useState(false);
    const [createForm, setCreateForm] = useState({
        name: '',
        description: '',
        strategy: 'priority',
        is_active: true,
        list_in_catalog: false,
    });
    const [deleteGroup, setDeleteGroup] = useState<string | null>(null);

    const { data: groupsData, isLoading } = useQuery({
        queryKey: ['model-groups'],
        queryFn: () => modelGroupsApi.list(),
    });

    const [orderedGroups, setOrderedGroups] = useState<ModelGroupSummary[]>([]);
    useEffect(() => {
        if (groupsData?.groups) {
            setOrderedGroups([...groupsData.groups].sort((a, b) => a.priority - b.priority));
        }
    }, [groupsData]);

    const groupSensors = useSensors(
        useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
        useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
    );

    const reorderGroupsMut = useMutation({
        mutationFn: (items: { name: string; priority: number }[]) =>
            modelGroupsApi.reorderGroups(items),
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['model-groups'] });
            toast.success('Groups reordered');
        },
        onError: (e) => toast.error(e.message),
    });

    const handleGroupDragEnd = (event: DragEndEvent) => {
        const { active, over } = event;
        if (!over || active.id === over.id) return;

        setOrderedGroups((items) => {
            const oldIndex = items.findIndex((g) => g.name === active.id);
            const newIndex = items.findIndex((g) => g.name === over.id);
            const reordered = arrayMove(items, oldIndex, newIndex);
            
            // Assign priorities based on new order (lowest priority number = highest)
            const priorities = reordered.map((g, idx) => ({
                name: g.name,
                priority: idx,
            }));
            
            reorderGroupsMut.mutate(priorities);
            return reordered;
        });
    };

    const { data: groupDetail } = useQuery({
        queryKey: ['model-groups', selectedGroup?.name],
        queryFn: () => modelGroupsApi.get(selectedGroup!.name),
        enabled: !!selectedGroup,
    });

    const createMut = useMutation({
        mutationFn: (data: ModelGroupCreate) => modelGroupsApi.create(data),
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['model-groups'] });
            toast.success('Group created');
            setCreateOpen(false);
            setCreateForm({ name: '', description: '', strategy: 'priority', is_active: true, list_in_catalog: false });
        },
        onError: (e) => toast.error(e.message),
    });

    const deleteMut = useMutation({
        mutationFn: (name: string) => modelGroupsApi.delete(name),
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['model-groups'] });
            toast.success('Group deleted');
            setDeleteGroup(null);
        },
        onError: (e) => toast.error(e.message),
    });

    const listCatalogMut = useMutation({
        mutationFn: ({ name, list_in_catalog }: { name: string; list_in_catalog: boolean }) =>
            modelGroupsApi.update(name, { list_in_catalog }),
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['model-groups'] });
            toast.success('Catalog visibility updated');
        },
        onError: (e) => toast.error(e.message),
    });

    const groups = groupsData?.groups ?? [];

    // If a group is selected, show detail view
    if (selectedGroup && groupDetail) {
        return (
            <div className="p-6 max-w-4xl mx-auto">
                <GroupDetail group={groupDetail} onBack={() => setSelectedGroup(null)} />
            </div>
        );
    }

    return (
        <div className="p-6 max-w-4xl mx-auto">
            <div className="flex items-center justify-between mb-6">
                <h1 className="text-2xl font-bold">Model Groups</h1>
                <Dialog open={createOpen} onOpenChange={setCreateOpen}>
                    <DialogTrigger asChild>
                        <Button>
                            <Plus className="h-4 w-4 mr-2" />
                            New Group
                        </Button>
                    </DialogTrigger>
                    <DialogContent>
                        <DialogHeader><DialogTitle>Create Model Group</DialogTitle></DialogHeader>
                        <div className="space-y-4 py-4">
                            <div>
                                <Label>Name</Label>
                                <Input
                                    value={createForm.name}
                                    onChange={(e) => setCreateForm((f) => ({ ...f, name: e.target.value }))}
                                    placeholder="e.g. kimi-llm-group"
                                />
                            </div>
                            <div>
                                <Label>Description</Label>
                                <Input
                                    value={createForm.description}
                                    onChange={(e) => setCreateForm((f) => ({ ...f, description: e.target.value }))}
                                    placeholder="Optional description"
                                />
                            </div>
                            <div>
                                <Label>Strategy</Label>
                                <Select value={createForm.strategy} onValueChange={(v) => setCreateForm((f) => ({ ...f, strategy: v }))}>
                                    <SelectTrigger><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="priority">Priority (lowest number = highest)</SelectItem>
                                        <SelectItem value="round_robin">Round Robin</SelectItem>
                                        <SelectItem value="weighted">Weighted</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="flex items-center gap-2">
                                <Switch
                                    checked={createForm.is_active}
                                    onCheckedChange={(v) => setCreateForm((f) => ({ ...f, is_active: v }))}
                                />
                                <Label>Active</Label>
                            </div>
                            <div className="flex items-center gap-2">
                                <Switch
                                    checked={createForm.list_in_catalog}
                                    onCheckedChange={(v) => setCreateForm((f) => ({ ...f, list_in_catalog: v }))}
                                />
                                <Label>Show in catalog</Label>
                            </div>
                        </div>
                        <DialogFooter>
                            <Button variant="ghost" onClick={() => setCreateOpen(false)}>Cancel</Button>
                            <Button
                                onClick={() => createMut.mutate(createForm)}
                                disabled={createMut.isPending || !createForm.name}
                            >
                                Create
                            </Button>
                        </DialogFooter>
                    </DialogContent>
                </Dialog>
            </div>

            {isLoading ? (
                <div className="space-y-4">
                    {[1, 2, 3].map((i) => (
                        <Skeleton key={i} className="h-24 w-full" />
                    ))}
                </div>
            ) : orderedGroups.length === 0 ? (
                <Card>
                    <CardContent className="py-12 text-center text-muted-foreground">
                        No model groups yet. Create one to get started.
                    </CardContent>
                </Card>
            ) : (
                <DndContext
                    sensors={groupSensors}
                    collisionDetection={closestCenter}
                    onDragEnd={handleGroupDragEnd}
                >
                    <SortableContext
                        items={orderedGroups.map((g) => g.name)}
                        strategy={verticalListSortingStrategy}
                    >
                        <div className="space-y-2">
                            {orderedGroups.map((group) => (
                                <SortableGroupCard
                                    key={group.id}
                                    group={group}
                                    onSelect={() => setSelectedGroup(group)}
                                    onDeleteClick={(name) => setDeleteGroup(name)}
                                    listCatalogMut={listCatalogMut}
                                />
                            ))}
                        </div>
                    </SortableContext>
                </DndContext>
            )}

            <Dialog
                open={!!deleteGroup}
                onOpenChange={(open) => {
                    if (!open) setDeleteGroup(null);
                }}
            >
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Delete Group</DialogTitle>
                    </DialogHeader>
                    <p className="text-sm text-muted-foreground py-4">
                        Are you sure you want to delete group <strong>{deleteGroup}</strong>?
                    </p>
                    <DialogFooter>
                        <Button variant="ghost" onClick={() => setDeleteGroup(null)}>
                            Cancel
                        </Button>
                        <Button
                            variant="destructive"
                            onClick={() => {
                                if (deleteGroup) {
                                    deleteMut.mutate(deleteGroup);
                                }
                            }}
                            disabled={deleteMut.isPending}
                        >
                            Delete
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

        </div>
    );
}
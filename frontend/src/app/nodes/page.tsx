'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { nodesApi, type Node, type CreateNode } from '@/lib/api';
import {
    BedrockAuthFields,
    inferBedrockAuthMode,
    isBedrockNodeFormValid,
    type BedrockAuthMode,
} from '@/components/nodes/bedrock-auth-fields';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Skeleton } from '@/components/ui/skeleton';
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogFooter,
    DialogTrigger,
} from '@/components/ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table';
import { toast } from 'sonner';
import { useState, useEffect } from 'react';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select';
import {
    Plus,
    RefreshCw,
    Heart,
    Server,
    Trash2,
    Pencil,
    Activity,
    BarChart3,
    Link as LinkIcon,
    GripVertical,
    Eye,
    EyeOff,
} from 'lucide-react';
import Link from 'next/link';
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
    rectSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';

function HealthBadge({ status }: { status: string }) {
    const map: Record<string, { variant: 'default' | 'secondary' | 'destructive' | 'outline'; label: string }> = {
        healthy: {
            variant: 'default',
            label: 'Healthy',
        },
        unhealthy: {
            variant: 'destructive',
            label: 'Unhealthy',
        },
        unknown: {
            variant: 'secondary',
            label: 'Unknown',
        },
    };
    const { label } = map[status] ?? { label: status };
    return (
        <Badge
            variant={map[status]?.variant ?? 'outline'}
            className={
                status === 'healthy'
                    ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
                    : status === 'unhealthy'
                    ? 'bg-destructive/20 text-destructive border-destructive/30'
                    : ''
            }
        >
            {label}
        </Badge>
    );
}

function NodeCard({
    node,
    onHealthCheck,
    onSync,
    onDelete,
    healthChecking,
    syncing,
    isOverlay,
}: {
    node: Node;
    onHealthCheck: (id: number) => void;
    onSync: (id: number) => void;
    onDelete: (id: number) => void;
    healthChecking: number | null;
    syncing: number | null;
    isOverlay?: boolean;
}) {
    const {
        attributes,
        listeners,
        setNodeRef,
        transform,
        transition,
        isDragging,
    } = useSortable({ id: node.id });

    const style = {
        transform: CSS.Transform.toString(transform),
        transition,
    };
    const qc = useQueryClient();
    const [editOpen, setEditOpen] = useState(false);
    const [showEditApiKey, setShowEditApiKey] = useState(false);
    const [form, setForm] = useState<CreateNode>({
        name: node.name,
        base_url: node.base_url,
        api_key: node.api_key ?? undefined,
        priority: node.priority,
        weight: node.weight,
        is_active: node.is_active,
        node_type: node.node_type,
        warmup_enabled: node.warmup_enabled,
        auto_sync_enabled: node.auto_sync_enabled,
        code: node.code,
        headers: node.headers ?? undefined,
        aws_secret_key: node.aws_secret_key ?? undefined,
        aws_region: node.aws_region ?? undefined,
        aws_session_token: node.aws_session_token ?? undefined,
        scoped_models: node.scoped_models,
        auto_cookie_refresh: node.auto_cookie_refresh,
    });
    const [headersStr, setHeadersStr] = useState<string>(
        node.headers ? JSON.stringify(node.headers, null, 2) : ''
    );
    const [bedrockAuthMode, setBedrockAuthMode] = useState<BedrockAuthMode>(() =>
        inferBedrockAuthMode(node),
    );

    const updateMut = useMutation({
        mutationFn: (data: Partial<CreateNode>) => nodesApi.update(node.id, data),
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['nodes'] });
            setEditOpen(false);
            toast.success('Node updated');
        },
        onError: (e) => toast.error(e.message),
    });

    const toggleMut = useMutation({
        mutationFn: (is_active: boolean) => nodesApi.update(node.id, { is_active }),
        onSuccess: (_data, is_active) => {
            qc.invalidateQueries({ queryKey: ['nodes'] });
            toast.success(is_active ? 'Node activated' : 'Node deactivated');
        },
        onError: (e) => toast.error(e.message),
    });

    useEffect(() => {
        if (editOpen) {
            setForm({
                name: node.name,
                base_url: node.base_url,
                api_key: node.api_key ?? undefined,
                priority: node.priority,
                weight: node.weight,
                is_active: node.is_active,
                node_type: node.node_type,
                warmup_enabled: node.warmup_enabled,
                auto_sync_enabled: node.auto_sync_enabled,
                code: node.code,
                headers: node.headers ?? undefined,
                aws_secret_key: node.aws_secret_key ?? undefined,
                aws_region: node.aws_region ?? undefined,
                aws_session_token: node.aws_session_token ?? undefined,
                scoped_models: node.scoped_models,
                auto_cookie_refresh: node.auto_cookie_refresh,
            });
            setHeadersStr(node.headers ? JSON.stringify(node.headers, null, 2) : '');
            setBedrockAuthMode(inferBedrockAuthMode(node));
        }
    }, [editOpen, node.name, node.base_url, node.api_key, node.priority, node.weight, node.is_active, node.node_type, node.warmup_enabled, node.auto_sync_enabled, node.code, node.headers, node.aws_secret_key, node.aws_region, node.aws_session_token, node.bedrock_auth_mode, node.scoped_models, node.auto_cookie_refresh]);

    const isInactive = !node.is_active;

    return (
        <Card
            ref={setNodeRef}
            style={style}
            className={`overflow-hidden ${isInactive ? 'opacity-60 bg-muted/30' : ''} ${isDragging ? 'ring-2 ring-primary z-50' : ''}`}
        >
            <CardHeader className="pb-2">
                <div className="flex items-start justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 min-w-0">
                        <button
                            className="cursor-grab active:cursor-grabbing text-muted-foreground hover:text-foreground shrink-0"
                            {...attributes}
                            {...listeners}
                        >
                            <GripVertical className="h-5 w-5" />
                        </button>
                        <CardTitle className="text-base shrink-0">{node.name}</CardTitle>
                        <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1">
                            <Badge
                                variant="outline"
                                className={`text-[10px] px-1.5 py-0 ${
                                    node.node_type === 'vllm'
                                        ? 'bg-purple-500/10 text-purple-400 border-purple-500/30'
                                        : node.node_type === 'ollama'
                                        ? 'bg-blue-500/10 text-blue-400 border-blue-500/30'
                                        : node.node_type === 'antigravity'
                                        ? 'bg-green-500/10 text-green-400 border-green-500/30'
                                        : node.node_type === 'bedrock'
                                        ? 'bg-orange-500/10 text-orange-400 border-orange-500/30'
                                        : ''
                                }`}
                            >
                                {node.node_type === 'vllm' ? 'vLLM' : node.node_type === 'ollama' ? 'Ollama' : node.node_type === 'antigravity' ? 'Antigravity' : node.node_type === 'bedrock' ? 'Bedrock' : node.node_type}
                            </Badge>
                            {node.code && (
                                <Badge variant="outline" className="text-[10px] px-1.5 py-0 font-mono text-cyan-400 border-cyan-400/30 bg-cyan-400/10">
                                    {node.code}
                                </Badge>
                            )}
                            {!node.warmup_enabled && (
                                <Badge variant="outline" className="text-[10px] px-1.5 py-0 text-amber-400 border-amber-400/30 bg-amber-400/10">
                                    Warmup Off
                                </Badge>
                            )}
                            {!node.auto_sync_enabled && (
                                <Badge variant="outline" className="text-[10px] px-1.5 py-0 text-sky-400 border-sky-400/30 bg-sky-400/10">
                                    Auto Sync Off
                                </Badge>
                            )}
                            {node.scoped_models && (
                                <Badge variant="outline" className="text-[10px] px-1.5 py-0 text-pink-400 border-pink-400/30 bg-pink-400/10">
                                    Scoped
                                </Badge>
                            )}
                            {node.auto_cookie_refresh && (
                                <Badge variant="outline" className="text-[10px] px-1.5 py-0 text-emerald-400 border-emerald-400/30 bg-emerald-400/10">
                                    Cookie Refresh
                                </Badge>
                            )}
                            {isInactive && (
                                <Badge variant="outline" className="text-[10px] px-1.5 py-0 text-muted-foreground">
                                    Inactive
                                </Badge>
                            )}
                        </div>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                        <Switch
                            checked={node.is_active}
                            onCheckedChange={(v) => toggleMut.mutate(v)}
                            disabled={toggleMut.isPending}
                        />
                        <HealthBadge status={node.health_status} />
                    </div>
                </div>
                <p className="text-xs text-muted-foreground font-mono mt-1 truncate" title={node.base_url}>
                    {node.base_url}
                </p>
            </CardHeader>
            <CardContent className="space-y-4">
                <div className="flex items-center justify-between text-sm">
                    <span className="text-muted-foreground">Models</span>
                    <Badge variant="outline">{node.model_count}</Badge>
                </div>
                <div className="flex flex-wrap gap-2">
                    <Button
                        size="sm"
                        variant="outline"
                        onClick={() => onHealthCheck(node.id)}
                        disabled={healthChecking !== null}
                    >
                        {healthChecking === node.id ? (
                            <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                            <Heart className="h-3.5 w-3.5" />
                        )}
                        <span className="ml-1">Health</span>
                    </Button>
                    <Button
                        size="sm"
                        variant="outline"
                        onClick={() => onSync(node.id)}
                        disabled={syncing !== null}
                    >
                        {syncing === node.id ? (
                            <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                            <RefreshCw className="h-3.5 w-3.5" />
                        )}
                        <span className="ml-1">Sync</span>
                    </Button>
                    <Dialog open={editOpen} onOpenChange={setEditOpen}>
                        <DialogTrigger asChild>
                            <Button size="sm" variant="outline">
                                <Pencil className="h-3.5 w-3.5" />
                                <span className="ml-1">Edit</span>
                            </Button>
                        </DialogTrigger>
                        <DialogContent className="max-h-[90vh] overflow-y-auto">
                            <DialogHeader>
                                <DialogTitle>Edit Node</DialogTitle>
                            </DialogHeader>
                            <div className="space-y-4 py-4">
                                <div>
                                    <Label>Name</Label>
                                    <Input
                                        value={form.name}
                                        onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                                    />
                                </div>
                                <div>
                                    <Label>Base URL</Label>
                                    <Input
                                        value={form.base_url}
                                        onChange={(e) => setForm((f) => ({ ...f, base_url: e.target.value }))}
                                    />
                                </div>
                                <div>
                                    <Label>API Key (optional)</Label>
                                    <div className="flex items-center gap-2">
                                        <Input
                                            type={showEditApiKey ? 'text' : 'password'}
                                            placeholder="Bearer token for this endpoint"
                                            value={form.api_key || ''}
                                            onChange={(e) => setForm((f) => ({ ...f, api_key: e.target.value || undefined }))}
                                        />
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            size="icon"
                                            onClick={() => setShowEditApiKey((s) => !s)}
                                        >
                                            {showEditApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                                        </Button>
                                    </div>
                                </div>
                                <div>
                                    <Label>Custom Headers (optional)</Label>
                                    <textarea
                                        className="flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                                        placeholder='{"X-Custom-Header": "value"}'
                                        value={headersStr}
                                        onChange={(e) => setHeadersStr(e.target.value)}
                                    />
                                    <p className="text-xs text-muted-foreground mt-1">
                                        JSON object with custom HTTP headers sent with every request to this node.
                                    </p>
                                </div>
                                <div>
                                    <Label>Code</Label>
                                    <Input
                                        placeholder="code"
                                        value={form.code || ''}
                                        onChange={(e) => setForm((f) => ({ ...f, code: e.target.value || undefined }))}
                                    />
                                    <p className="text-xs text-muted-foreground mt-1">
                                        1-30 chars, lowercase alphanumeric with hyphens/underscores
                                    </p>
                                </div>
                                <div>
                                    <Label>Node Type</Label>
                                    <Select
                                        value={form.node_type || 'ollama'}
                                        onValueChange={(v) => setForm((f) => ({ ...f, node_type: v }))}
                                    >
                                        <SelectTrigger>
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="ollama">Ollama</SelectItem>
                                            <SelectItem value="vllm">vLLM (OpenAI-compatible)</SelectItem>
                                            <SelectItem value="antigravity">Antigravity (Google v1internal)</SelectItem>
                                            <SelectItem value="bedrock">AWS Bedrock</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>
                                {form.node_type === 'bedrock' && (
                                    <BedrockAuthFields
                                        form={form}
                                        setForm={setForm}
                                        authMode={bedrockAuthMode}
                                        onAuthModeChange={(mode) => {
                                            setBedrockAuthMode(mode);
                                            if (mode === 'api_key') {
                                                setForm((f) => ({
                                                    ...f,
                                                    aws_secret_key: undefined,
                                                    aws_session_token: undefined,
                                                }));
                                            }
                                        }}
                                    />
                                )}
                                <div className="grid grid-cols-2 gap-4">
                                    <div>
                                        <Label>Priority</Label>
                                        <Input
                                            type="number"
                                            value={form.priority}
                                            onChange={(e) =>
                                                setForm((f) => ({ ...f, priority: Number(e.target.value) || 0 }))
                                            }
                                        />
                                    </div>
                                    <div>
                                        <Label>Weight</Label>
                                        <Input
                                            type="number"
                                            value={form.weight}
                                            onChange={(e) =>
                                                setForm((f) => ({ ...f, weight: Number(e.target.value) || 100 }))
                                            }
                                        />
                                    </div>
                                </div>
                                <div className="flex items-center gap-2">
                                    <Switch
                                        checked={form.warmup_enabled}
                                        onCheckedChange={(v) => setForm((f) => ({ ...f, warmup_enabled: v }))}
                                    />
                                    <Label>Warmup Enabled</Label>
                                </div>
                                <div className="flex items-center gap-2">
                                    <Switch
                                        checked={form.auto_sync_enabled}
                                        onCheckedChange={(v) => setForm((f) => ({ ...f, auto_sync_enabled: v }))}
                                    />
                                    <Label>Auto Sync Enabled</Label>
                                    <p className="text-xs text-muted-foreground">
                                        When off, periodic model discovery skips this node.
                                    </p>
                                </div>
                                <div className="flex items-center gap-2">
                                    <Switch
                                        checked={form.is_active}
                                        onCheckedChange={(v) => setForm((f) => ({ ...f, is_active: v }))}
                                    />
                                    <Label>Active</Label>
                                </div>
                                <div className="flex items-center gap-2">
                                    <Switch
                                        checked={form.scoped_models}
                                        onCheckedChange={(v) => setForm((f) => ({ ...f, scoped_models: v }))}
                                    />
                                    <Label>Scoped Models</Label>
                                    <p className="text-xs text-muted-foreground">
                                        When enabled, models on this node are only accessible via node:code:model prefix.
                                    </p>
                                </div>
                                <div className="flex items-center gap-2">
                                    <Switch
                                        checked={form.auto_cookie_refresh}
                                        onCheckedChange={(v) => setForm((f) => ({ ...f, auto_cookie_refresh: v }))}
                                    />
                                    <Label>Auto Cookie Refresh</Label>
                                    <p className="text-xs text-muted-foreground">
                                        Automatically capture WAF challenge cookies on this node.
                                    </p>
                                </div>
                            </div>
                            <DialogFooter>
                                <Button variant="ghost" onClick={() => setEditOpen(false)}>
                                    Cancel
                                </Button>
                                <Button
                                    onClick={() => {
                                        const payload = { ...form };
                                        if (headersStr.trim()) {
                                            try {
                                                payload.headers = JSON.parse(headersStr);
                                            } catch {
                                                toast.error('Invalid JSON in Custom Headers');
                                                return;
                                            }
                                        } else {
                                            payload.headers = undefined;
                                        }
                                        if (form.node_type === 'bedrock') {
                                            payload.bedrock_auth_mode = bedrockAuthMode;
                                            if (bedrockAuthMode === 'api_key') {
                                                payload.aws_secret_key = undefined;
                                                payload.aws_session_token = undefined;
                                            }
                                        }
                                        updateMut.mutate(payload);
                                    }}
                                    disabled={
                                        updateMut.isPending ||
                                        (form.node_type === 'bedrock' &&
                                            !isBedrockNodeFormValid(form, bedrockAuthMode))
                                    }
                                >
                                    Save
                                </Button>
                            </DialogFooter>
                        </DialogContent>
                    </Dialog>
                    <Link href={`/nodes/${node.id}`}>
                        <Button size="sm" variant="outline">
                            <Activity className="h-3.5 w-3.5" />
                            <span className="ml-1">Details</span>
                        </Button>
                    </Link>
                    <Dialog>
                        <DialogTrigger asChild>
                            <Button size="sm" variant="ghost" className="text-destructive hover:text-destructive">
                                <Trash2 className="h-3.5 w-3.5" />
                            </Button>
                        </DialogTrigger>
                        <DialogContent>
                            <DialogHeader>
                                <DialogTitle>Delete Node</DialogTitle>
                            </DialogHeader>
                            <p className="text-sm text-muted-foreground py-4">
                                Are you sure you want to delete node <strong>{node.name}</strong>? This will remove all
                                model associations.
                            </p>
                            <DialogFooter>
                                <Button variant="ghost">Cancel</Button>
                                <Button variant="destructive" onClick={() => onDelete(node.id)}>
                                    Delete
                                </Button>
                            </DialogFooter>
                        </DialogContent>
                    </Dialog>
                </div>
            </CardContent>
        </Card>
    );
}

function AddNodeDialog({ onSuccess }: { onSuccess: () => void }) {
    const [open, setOpen] = useState(false);
    const [showAddApiKey, setShowAddApiKey] = useState(false);
    const [form, setForm] = useState<CreateNode>({
        name: '',
        base_url: 'http://localhost:11434',
        priority: 0,
        weight: 100,
        is_active: true,
        node_type: 'ollama',
        warmup_enabled: true,
        auto_sync_enabled: true,
        code: null,
        aws_secret_key: undefined,
        aws_region: undefined,
        aws_session_token: undefined,
        scoped_models: false,
        auto_cookie_refresh: false,
    });
    const [headersStr, setHeadersStr] = useState('');
    const [bedrockAuthMode, setBedrockAuthMode] = useState<BedrockAuthMode>('iam');
    const qc = useQueryClient();
    const mut = useMutation({
        mutationFn: nodesApi.create,
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['nodes'] });
            setOpen(false);
            setForm({ name: '', base_url: 'http://localhost:11434', priority: 0, weight: 100, is_active: true, node_type: 'ollama', warmup_enabled: true, auto_sync_enabled: true, code: null, headers: undefined, aws_secret_key: undefined, aws_region: undefined, aws_session_token: undefined, scoped_models: false, auto_cookie_refresh: false });
            setHeadersStr('');
            toast.success('Node created');
            onSuccess();
        },
        onError: (e) => toast.error(e.message),
    });

    return (
        <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
                <Button>
                    <Plus className="h-4 w-4 mr-2" />
                    Add Node
                </Button>
            </DialogTrigger>
            <DialogContent className="max-h-[90vh] overflow-y-auto">
                <DialogHeader>
                    <DialogTitle>Add Node</DialogTitle>
                </DialogHeader>
                <div className="space-y-4 py-4">
                    <div>
                        <Label>Name</Label>
                        <Input
                            placeholder={form.node_type === 'vllm' ? 'vllm-endpoint' : 'main-server'}
                            value={form.name}
                            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                        />
                    </div>
                    <div>
                        <Label>Base URL</Label>
                        <Input
                            placeholder={form.node_type === 'vllm' ? 'https://api.example.com/llm/model' : 'http://192.168.1.10:11434'}
                            value={form.base_url}
                            onChange={(e) => setForm((f) => ({ ...f, base_url: e.target.value }))}
                        />
                    </div>
                    <div>
                        <Label>API Key (optional)</Label>
                        <div className="flex items-center gap-2">
                            <Input
                                type={showAddApiKey ? 'text' : 'password'}
                                placeholder="Bearer token for this endpoint"
                                value={form.api_key || ''}
                                onChange={(e) => setForm((f) => ({ ...f, api_key: e.target.value || undefined }))}
                            />
                            <Button
                                type="button"
                                variant="ghost"
                                size="icon"
                                onClick={() => setShowAddApiKey((s) => !s)}
                            >
                                {showAddApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                            </Button>
                        </div>
                    </div>
                    <div>
                        <Label>Custom Headers (optional)</Label>
                        <textarea
                            className="flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                            placeholder='{"X-Custom-Header": "value"}'
                            value={headersStr}
                            onChange={(e) => setHeadersStr(e.target.value)}
                        />
                        <p className="text-xs text-muted-foreground mt-1">
                            JSON object with custom HTTP headers sent with every request to this node.
                        </p>
                    </div>
                    <div>
                        <Label>Code</Label>
                        <Input
                            placeholder="code"
                            value={form.code || ''}
                            onChange={(e) => setForm((f) => ({ ...f, code: e.target.value || undefined }))}
                        />
                        <p className="text-xs text-muted-foreground mt-1">
                            1-30 chars, lowercase alphanumeric with hyphens/underscores
                        </p>
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                        <div>
                            <Label>Priority (higher = preferred)</Label>
                            <Input
                                type="number"
                                value={form.priority}
                                onChange={(e) =>
                                    setForm((f) => ({ ...f, priority: Number(e.target.value) || 0 }))
                                }
                            />
                        </div>
                        <div>
                            <Label>Weight (load balancing)</Label>
                            <Input
                                type="number"
                                value={form.weight}
                                onChange={(e) =>
                                    setForm((f) => ({ ...f, weight: Number(e.target.value) || 100 }))
                                }
                            />
                        </div>
                    </div>
                    <div>
                        <Label>Node Type</Label>
                        <Select
                            value={form.node_type || 'ollama'}
                            onValueChange={(v) => setForm((f) => ({ ...f, node_type: v }))}
                        >
                            <SelectTrigger><SelectValue /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="ollama">Ollama</SelectItem>
                                <SelectItem value="vllm">vLLM (OpenAI-compatible)</SelectItem>
                                <SelectItem value="antigravity">Antigravity (Google v1internal)</SelectItem>
                                <SelectItem value="bedrock">AWS Bedrock</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>
                    {form.node_type === 'bedrock' && (
                        <BedrockAuthFields
                            form={form}
                            setForm={setForm}
                            authMode={bedrockAuthMode}
                            onAuthModeChange={(mode) => {
                                setBedrockAuthMode(mode);
                                if (mode === 'api_key') {
                                    setForm((f) => ({
                                        ...f,
                                        aws_secret_key: undefined,
                                        aws_session_token: undefined,
                                    }));
                                }
                            }}
                        />
                    )}
                    <div className="flex items-center gap-2">
                        <Switch
                            checked={form.warmup_enabled}
                            onCheckedChange={(v) => setForm((f) => ({ ...f, warmup_enabled: v }))}
                        />
                        <Label>Warmup Enabled</Label>
                    </div>
                    <div className="flex items-center gap-2">
                        <Switch
                            checked={form.auto_sync_enabled}
                            onCheckedChange={(v) => setForm((f) => ({ ...f, auto_sync_enabled: v }))}
                        />
                        <Label>Auto Sync Enabled</Label>
                        <p className="text-xs text-muted-foreground">
                            When off, periodic model discovery skips this node.
                        </p>
                    </div>
                    <div className="flex items-center gap-2">
                        <Switch
                            checked={form.is_active}
                            onCheckedChange={(v) => setForm((f) => ({ ...f, is_active: v }))}
                        />
                        <Label>Active</Label>
                    </div>
                    <div className="flex items-center gap-2">
                        <Switch
                            checked={form.scoped_models}
                            onCheckedChange={(v) => setForm((f) => ({ ...f, scoped_models: v }))}
                        />
                        <Label>Scoped Models</Label>
                        <p className="text-xs text-muted-foreground">
                            When enabled, models on this node are only accessible via node:code:model prefix.
                        </p>
                    </div>
                    <div className="flex items-center gap-2">
                        <Switch
                            checked={form.auto_cookie_refresh}
                            onCheckedChange={(v) => setForm((f) => ({ ...f, auto_cookie_refresh: v }))}
                        />
                        <Label>Auto Cookie Refresh</Label>
                        <p className="text-xs text-muted-foreground">
                            Automatically capture WAF challenge cookies on this node.
                        </p>
                    </div>
                </div>
                <DialogFooter>
                    <Button variant="ghost" onClick={() => setOpen(false)}>
                        Cancel
                    </Button>
                    <Button
                        onClick={() => {
                            const payload = { ...form };
                            if (headersStr.trim()) {
                                try {
                                    payload.headers = JSON.parse(headersStr);
                                } catch {
                                    toast.error('Invalid JSON in Custom Headers');
                                    return;
                                }
                            } else {
                                payload.headers = undefined;
                            }
                            if (form.node_type === 'bedrock') {
                                payload.bedrock_auth_mode = bedrockAuthMode;
                                if (bedrockAuthMode === 'api_key') {
                                    payload.aws_secret_key = undefined;
                                    payload.aws_session_token = undefined;
                                }
                            }
                            mut.mutate(payload);
                        }}
                        disabled={
                            !form.name ||
                            (form.node_type !== 'antigravity' &&
                                form.node_type !== 'bedrock' &&
                                !form.base_url) ||
                            (form.node_type === 'bedrock' &&
                                !isBedrockNodeFormValid(form, bedrockAuthMode)) ||
                            mut.isPending
                        }
                    >
                        Create
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

export default function NodesPage() {
    const qc = useQueryClient();
    const [healthChecking, setHealthChecking] = useState<number | null>(null);
    const [syncing, setSyncing] = useState<number | null>(null);

    const { data: nodes, isLoading } = useQuery({
        queryKey: ['nodes'],
        queryFn: () => nodesApi.list(),
    });

    // Local ordered state for drag-and-drop (sorted by priority desc)
    const [orderedNodes, setOrderedNodes] = useState<Node[]>([]);
    useEffect(() => {
        if (nodes) {
            setOrderedNodes([...nodes].sort((a, b) => b.priority - a.priority));
        }
    }, [nodes]);

    const { data: distribution } = useQuery({
        queryKey: ['nodes-distribution'],
        queryFn: nodesApi.getDistribution,
    });

    const { data: loadStatus } = useQuery({
        queryKey: ['nodes-load-status'],
        queryFn: nodesApi.getLoadStatus,
    });

    const syncAllMut = useMutation({
        mutationFn: nodesApi.syncAll,
        onSuccess: (results) => {
            qc.invalidateQueries({ queryKey: ['nodes'] });
            qc.invalidateQueries({ queryKey: ['nodes-distribution'] });
            const total = results.reduce((a, r) => a + r.synced_count, 0);
            toast.success(`Synced ${results.length} nodes, ${total} models total`);
        },
        onError: (e) => toast.error(e.message),
    });

    const deleteMut = useMutation({
        mutationFn: nodesApi.delete,
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['nodes'] });
            qc.invalidateQueries({ queryKey: ['nodes-distribution'] });
            toast.success('Node deleted');
        },
        onError: (e) => toast.error(e.message),
    });

    const priorityMut = useMutation({
        mutationFn: nodesApi.updatePriorities,
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['nodes'] });
            toast.success('Priorities updated');
        },
        onError: (e) => toast.error(e.message),
    });

    const sensors = useSensors(
        useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
        useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
    );

    const handleDragEnd = (event: DragEndEvent) => {
        const { active, over } = event;
        if (!over || active.id === over.id) return;

        setOrderedNodes((items) => {
            const oldIndex = items.findIndex((n) => n.id === active.id);
            const newIndex = items.findIndex((n) => n.id === over.id);
            const reordered = arrayMove(items, oldIndex, newIndex);
            // Assign priorities based on new order (highest first)
            const priorities = reordered.map((node, idx) => ({
                node_id: node.id,
                priority: reordered.length - idx,
            }));
            priorityMut.mutate(priorities);
            return reordered;
        });
    };

    const handleHealthCheck = async (id: number) => {
        setHealthChecking(id);
        try {
            const r = await nodesApi.healthCheck(id);
            toast.success(`${r.node_name}: ${r.health_status}`);
            qc.invalidateQueries({ queryKey: ['nodes'] });
        } catch (e) {
            toast.error(e instanceof Error ? e.message : 'Health check failed');
        } finally {
            setHealthChecking(null);
        }
    };

    const handleSync = async (id: number) => {
        setSyncing(id);
        try {
            const r = await nodesApi.syncModels(id);
            toast.success(`${r.node_name}: ${r.synced_count} models synced`);
            qc.invalidateQueries({ queryKey: ['nodes'] });
            qc.invalidateQueries({ queryKey: ['nodes-distribution'] });
        } catch (e) {
            toast.error(e instanceof Error ? e.message : 'Sync failed');
        } finally {
            setSyncing(null);
        }
    };

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <h1 className="text-2xl font-semibold">Load Balancing & Nodes</h1>
                <div className="flex gap-2">
                    <AddNodeDialog onSuccess={() => {}} />
                    <Button
                        variant="outline"
                        onClick={() => syncAllMut.mutate()}
                        disabled={syncAllMut.isPending || !nodes?.length}
                    >
                        {syncAllMut.isPending ? (
                            <RefreshCw className="h-4 w-4 animate-spin" />
                        ) : (
                            <RefreshCw className="h-4 w-4" />
                        )}
                        <span className="ml-2">Sync All</span>
                    </Button>
                </div>
            </div>

            <Tabs defaultValue="nodes">
                <TabsList>
                    <TabsTrigger value="nodes">
                        <Server className="h-4 w-4 mr-2" />
                        Nodes ({nodes?.length ?? 0})
                    </TabsTrigger>
                    <TabsTrigger value="distribution">
                        <BarChart3 className="h-4 w-4 mr-2" />
                        Model Distribution
                    </TabsTrigger>
                    <TabsTrigger value="load">
                        <Activity className="h-4 w-4 mr-2" />
                        Load Status
                    </TabsTrigger>
                </TabsList>

                <TabsContent value="nodes" className="mt-4">
                    {isLoading ? (
                        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                            {[1, 2, 3].map((i) => (
                                <Skeleton key={i} className="h-48" />
                            ))}
                        </div>
                    ) : orderedNodes?.length === 0 ? (
                        <Card>
                            <CardContent className="flex flex-col items-center justify-center py-16">
                                <Server className="h-12 w-12 text-muted-foreground mb-4" />
                                <p className="text-muted-foreground mb-2">No nodes configured</p>
                                <p className="text-sm text-muted-foreground mb-4">
                                    Add your first node (Ollama or vLLM) to enable load balancing
                                </p>
                                <AddNodeDialog onSuccess={() => {}} />
                            </CardContent>
                        </Card>
                    ) : (
                        <DndContext
                            sensors={sensors}
                            collisionDetection={closestCenter}
                            onDragEnd={handleDragEnd}
                        >
                            <SortableContext
                                items={orderedNodes.map((n) => n.id)}
                                strategy={rectSortingStrategy}
                            >
                                <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                                    {orderedNodes.map((node) => (
                                        <NodeCard
                                            key={node.id}
                                            node={node}
                                            onHealthCheck={handleHealthCheck}
                                            onSync={handleSync}
                                            onDelete={(id) => deleteMut.mutate(id)}
                                            healthChecking={healthChecking}
                                            syncing={syncing}
                                        />
                                    ))}
                                </div>
                            </SortableContext>
                        </DndContext>
                    )}
                </TabsContent>

                <TabsContent value="distribution" className="mt-4">
                    <Card>
                        <CardHeader>
                            <CardTitle className="text-sm font-medium">Model Distribution</CardTitle>
                            <p className="text-xs text-muted-foreground">
                                Which models are available on which nodes
                            </p>
                        </CardHeader>
                        <CardContent>
                            {!distribution?.length ? (
                                <p className="text-sm text-muted-foreground py-8 text-center">
                                    No model distribution data. Sync nodes first.
                                </p>
                            ) : (
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Model</TableHead>
                                            <TableHead>Node Count</TableHead>
                                            <TableHead>Nodes</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {distribution.map((d) => (
                                            <TableRow key={d.model_name}>
                                                <TableCell className="font-mono text-sm">{d.model_name}</TableCell>
                                                <TableCell>
                                                    <Badge
                                                        variant="outline"
                                                        className={
                                                            d.node_count >= 2
                                                                ? 'border-emerald-500/50 text-emerald-400'
                                                                : d.node_count === 1
                                                                ? 'border-amber-500/50 text-amber-400'
                                                                : ''
                                                        }
                                                    >
                                                        {d.node_count}
                                                    </Badge>
                                                </TableCell>
                                                <TableCell className="text-sm text-muted-foreground">
                                                    {d.nodes.join(', ')}
                                                </TableCell>
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            )}
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="load" className="mt-4">
                    <Card>
                        <CardHeader>
                            <CardTitle className="text-sm font-medium">Load Balancer Status</CardTitle>
                            <p className="text-xs text-muted-foreground">Current node status and metrics</p>
                        </CardHeader>
                        <CardContent>
                            {!loadStatus?.length ? (
                                <p className="text-sm text-muted-foreground py-8 text-center">
                                    No nodes or load data available
                                </p>
                            ) : (
                                <div className="space-y-4">
                                    {loadStatus.map((s) => (
                                        <div
                                            key={s.id}
                                            className="flex items-center justify-between rounded-lg border p-4"
                                        >
                                            <div className="flex items-center gap-4">
                                                <LinkIcon className="h-5 w-5 text-muted-foreground" />
                                                <div>
                                                    <p className="font-medium">{s.name}</p>
                                                    <p className="text-xs text-muted-foreground font-mono">
                                                        {s.base_url}
                                                    </p>
                                                </div>
                                                <HealthBadge status={s.health_status} />
                                                {!s.is_active && (
                                                    <Badge variant="outline">Inactive</Badge>
                                                )}
                                            </div>
                                            <Badge variant="secondary">{s.model_count} models</Badge>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </CardContent>
                    </Card>
                </TabsContent>
            </Tabs>
        </div>
    );
}

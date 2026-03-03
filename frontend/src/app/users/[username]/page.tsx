'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'next/navigation';
import { usersApi, modelMappingsApi } from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import { Skeleton } from '@/components/ui/skeleton';
import { Separator } from '@/components/ui/separator';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { toast } from 'sonner';
import { useState, useEffect } from 'react';
import { Copy, Save, X, ArrowLeft } from 'lucide-react';
import Link from 'next/link';

export default function UserDetailPage() {
    const params = useParams();
    const username = params.username as string;
    const queryClient = useQueryClient();

    const { data: user, isLoading } = useQuery({
        queryKey: ['users', username],
        queryFn: () => usersApi.get(username),
    });

    const { data: userModels } = useQuery({
        queryKey: ['users', username, 'models'],
        queryFn: () => usersApi.getModels(username),
    });

    const { data: allMappings } = useQuery({
        queryKey: ['model-mappings'],
        queryFn: modelMappingsApi.list,
    });

    const { data: limits } = useQuery({
        queryKey: ['users', username, 'limits'],
        queryFn: () => usersApi.getLimits(username),
    });

    const { data: activity } = useQuery({
        queryKey: ['users', username, 'activity'],
        queryFn: () => usersApi.getActivity(username),
    });

    const [reqLimit, setReqLimit] = useState('');
    const [tokenLimit, setTokenLimit] = useState('');
    const [hasAllModels, setHasAllModels] = useState(false);
    const [selectedModels, setSelectedModels] = useState<string[]>([]);

    useEffect(() => {
        if (limits) {
            // eslint-disable-next-line react-hooks/set-state-in-effect
            setReqLimit(limits.request_limit?.toString() || '');
            // eslint-disable-next-line react-hooks/set-state-in-effect
            setTokenLimit(limits.token_limit?.toString() || '');
        }
    }, [limits]);

    useEffect(() => {
        if (userModels) {
            // eslint-disable-next-line react-hooks/set-state-in-effect
            setHasAllModels(userModels.has_all_models);
            // eslint-disable-next-line react-hooks/set-state-in-effect
            setSelectedModels(userModels.models);
        }
    }, [userModels]);

    const modelsMutation = useMutation({
        mutationFn: () =>
            hasAllModels
                ? usersApi.setAllModels(username)
                : usersApi.setModels(username, selectedModels),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['users', username, 'models'] });
            toast.success('Model access updated');
        },
        onError: (err) => toast.error(err.message),
    });

    const limitsMutation = useMutation({
        mutationFn: () =>
            usersApi.setLimits(username, {
                request_limit: reqLimit ? parseInt(reqLimit) : undefined,
                token_limit: tokenLimit ? parseInt(tokenLimit) : undefined,
            }),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['users', username, 'limits'] });
            toast.success('Limits updated');
        },
        onError: (err) => toast.error(err.message),
    });

    const removeLimitsMutation = useMutation({
        mutationFn: () => usersApi.removeLimits(username),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['users', username, 'limits'] });
            setReqLimit('');
            setTokenLimit('');
            toast.success('Limits removed');
        },
        onError: (err) => toast.error(err.message),
    });

    if (isLoading || !user) {
        return <Skeleton className="h-96 w-full" />;
    }

    const toggleModel = (model: string) => {
        setSelectedModels((prev) =>
            prev.includes(model) ? prev.filter((m) => m !== model) : [...prev, model]
        );
    };

    return (
        <div className="space-y-6">
            <div className="flex items-center gap-3">
                <Link href="/users">
                    <Button variant="ghost" size="icon"><ArrowLeft className="h-4 w-4" /></Button>
                </Link>
                <div>
                    <h2 className="text-xl font-bold">{user.username}</h2>
                    <p className="text-sm text-muted-foreground">
                        Created: {new Date(user.created_at).toLocaleDateString('en-US')}
                    </p>
                </div>
                <Badge className="ml-2" variant={user.is_active ? 'default' : 'secondary'}>
                    {user.is_active ? 'Active' : 'Inactive'}
                </Badge>
            </div>

            <Card>
                <CardHeader className="pb-3">
                    <CardTitle className="text-sm">API Token</CardTitle>
                </CardHeader>
                <CardContent>
                    <div className="flex items-center gap-2">
                        <code className="flex-1 text-xs bg-muted px-3 py-2 rounded font-mono overflow-auto">
                            {user.token}
                        </code>
                        <Button
                            variant="outline" size="sm"
                            onClick={() => { navigator.clipboard.writeText(user.token); toast.success('Copied'); }}
                        >
                            <Copy className="h-4 w-4 mr-1" /> Copy
                        </Button>
                    </div>
                </CardContent>
            </Card>

            <Tabs defaultValue="models">
                <TabsList>
                    <TabsTrigger value="models">Models</TabsTrigger>
                    <TabsTrigger value="limits">Limits</TabsTrigger>
                    <TabsTrigger value="activity">Activity</TabsTrigger>
                </TabsList>

                <TabsContent value="models" className="mt-4">
                    <Card>
                        <CardHeader className="pb-3">
                            <div className="flex items-center justify-between">
                                <CardTitle className="text-sm">Model Access</CardTitle>
                                <Button size="sm" onClick={() => modelsMutation.mutate()} disabled={modelsMutation.isPending}>
                                    <Save className="h-4 w-4 mr-1" /> Save
                                </Button>
                            </div>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="flex items-center gap-3">
                                <Switch checked={hasAllModels} onCheckedChange={setHasAllModels} />
                                <span className="text-sm">Access to all models</span>
                            </div>
                            {!hasAllModels && allMappings && (
                                <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
                                    {allMappings.map((m) => (
                                        <label key={m.display_name} className="flex items-center gap-2 p-2 rounded-lg hover:bg-accent cursor-pointer">
                                            <input
                                                type="checkbox"
                                                checked={selectedModels.includes(m.display_name)}
                                                onChange={() => toggleModel(m.display_name)}
                                                className="rounded border-border"
                                            />
                                            <span className="text-sm">{m.display_name}</span>
                                        </label>
                                    ))}
                                </div>
                            )}
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="limits" className="mt-4">
                    <Card>
                        <CardHeader className="pb-3">
                            <div className="flex items-center justify-between">
                                <CardTitle className="text-sm">Usage Limits</CardTitle>
                                <div className="flex gap-2">
                                    <Button variant="ghost" size="sm" onClick={() => removeLimitsMutation.mutate()}>
                                        <X className="h-4 w-4 mr-1" /> Remove Limits
                                    </Button>
                                    <Button size="sm" onClick={() => limitsMutation.mutate()} disabled={limitsMutation.isPending}>
                                        <Save className="h-4 w-4 mr-1" /> Save
                                    </Button>
                                </div>
                            </div>
                        </CardHeader>
                        <CardContent>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <div>
                                    <label className="text-sm text-muted-foreground">Request Limit</label>
                                    <Input
                                        type="number" placeholder="Unlimited"
                                        value={reqLimit} onChange={(e) => setReqLimit(e.target.value)}
                                    />
                                </div>
                                <div>
                                    <label className="text-sm text-muted-foreground">Token Limit</label>
                                    <Input
                                        type="number" placeholder="Unlimited"
                                        value={tokenLimit} onChange={(e) => setTokenLimit(e.target.value)}
                                    />
                                </div>
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="activity" className="mt-4">
                    <Card>
                        <CardContent className="p-0">
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>Model</TableHead>
                                        <TableHead>Endpoint</TableHead>
                                        <TableHead>Token</TableHead>
                                        <TableHead>Duration</TableHead>
                                        <TableHead>Status</TableHead>
                                        <TableHead>Time</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {activity?.map((log) => (
                                        <TableRow key={log.id}>
                                            <TableCell className="font-mono text-xs">{log.model}</TableCell>
                                            <TableCell className="text-xs">{log.endpoint}</TableCell>
                                            <TableCell className="text-xs">{log.tokens_used.toLocaleString()}</TableCell>
                                            <TableCell className="text-xs">{log.request_time.toFixed(1)}s</TableCell>
                                            <TableCell>
                                                <Badge variant={log.status_code === 200 ? 'default' : 'destructive'} className="text-xs">
                                                    {log.status_code}
                                                </Badge>
                                            </TableCell>
                                            <TableCell className="text-xs text-muted-foreground">
                                                {new Date(log.created_at).toLocaleString('en-US')}
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                    {(!activity || activity.length === 0) && (
                                        <TableRow>
                                            <TableCell colSpan={6} className="text-center text-muted-foreground py-12">
                                                No activity yet
                                            </TableCell>
                                        </TableRow>
                                    )}
                                </TableBody>
                            </Table>
                        </CardContent>
                    </Card>
                </TabsContent>
            </Tabs>
        </div>
    );
}

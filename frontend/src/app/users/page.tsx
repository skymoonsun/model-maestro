'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { usersApi, type User } from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import {
    Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter, DialogClose,
} from '@/components/ui/dialog';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from 'sonner';
import { useState } from 'react';
import { Plus, Copy, Trash2, RefreshCw, Search, UserPlus, Pencil } from 'lucide-react';
import Link from 'next/link';

function CreateUserDialog() {
    const [username, setUsername] = useState('');
    const [open, setOpen] = useState(false);
    const queryClient = useQueryClient();

    const createMutation = useMutation({
        mutationFn: (name: string) => usersApi.create(name),
        onSuccess: (data) => {
            queryClient.invalidateQueries({ queryKey: ['users'] });
            toast.success(`User "${data.username}" created`);
            navigator.clipboard.writeText(data.token);
            toast.info('Token copied to clipboard');
            setUsername('');
            setOpen(false);
        },
        onError: (err) => toast.error(`Error: ${err.message}`),
    });

    return (
        <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
                <Button size="sm">
                    <UserPlus className="h-4 w-4 mr-2" /> New User
                </Button>
            </DialogTrigger>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>Create New User</DialogTitle>
                </DialogHeader>
                <div className="space-y-4 py-4">
                    <Input
                        placeholder="Username"
                        value={username}
                        onChange={(e) => setUsername(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && username.trim() && createMutation.mutate(username.trim())}
                    />
                </div>
                <DialogFooter>
                    <DialogClose asChild><Button variant="ghost">Cancel</Button></DialogClose>
                    <Button
                        onClick={() => username.trim() && createMutation.mutate(username.trim())}
                        disabled={!username.trim() || createMutation.isPending}
                    >
                        {createMutation.isPending ? 'Creating...' : 'Create'}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

export default function UsersPage() {
    const [search, setSearch] = useState('');
    const queryClient = useQueryClient();
    const { data: users, isLoading } = useQuery({
        queryKey: ['users'],
        queryFn: usersApi.list,
    });

    const deleteMutation = useMutation({
        mutationFn: usersApi.delete,
        onSuccess: (_, username) => {
            queryClient.invalidateQueries({ queryKey: ['users'] });
            toast.success(`"${username}" deleted`);
        },
        onError: (err) => toast.error(err.message),
    });

    const tokenMutation = useMutation({
        mutationFn: usersApi.regenerateToken,
        onSuccess: (data) => {
            queryClient.invalidateQueries({ queryKey: ['users'] });
            navigator.clipboard.writeText(data.token);
            toast.success('New token generated and copied');
        },
        onError: (err) => toast.error(err.message),
    });

    const filtered = users?.filter((u) =>
        u.username.toLowerCase().includes(search.toLowerCase())
    ) || [];

    if (isLoading) {
        return (
            <Card>
                <CardContent className="p-6"><Skeleton className="h-64 w-full" /></CardContent>
            </Card>
        );
    }

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between gap-4">
                <div className="relative flex-1 max-w-sm">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input
                        placeholder="Search users..."
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        className="pl-9"
                    />
                </div>
                <CreateUserDialog />
            </div>

            <Card>
                <CardContent className="p-0">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>User</TableHead>
                                <TableHead>Status</TableHead>
                                <TableHead>Models</TableHead>
                                <TableHead>Token</TableHead>
                                <TableHead>Created At</TableHead>
                                <TableHead className="text-right">Actions</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {filtered.map((user) => (
                                <TableRow key={user.username}>
                                    <TableCell>
                                        <Link href={`/users/${user.username}`} className="font-medium hover:underline">
                                            {user.username}
                                        </Link>
                                    </TableCell>
                                    <TableCell>
                                        <Badge variant={user.is_active ? 'default' : 'secondary'}>
                                            {user.is_active ? 'Active' : 'Inactive'}
                                        </Badge>
                                    </TableCell>
                                    <TableCell>
                                        {user.has_all_models ? (
                                            <Badge variant="outline" className="text-emerald-400 border-emerald-400/30">All</Badge>
                                        ) : (
                                            <span className="text-sm text-muted-foreground">{user.models?.length || 0} models</span>
                                        )}
                                    </TableCell>
                                    <TableCell>
                                        <div className="flex items-center gap-1">
                                            <code className="text-xs bg-muted px-2 py-1 rounded font-mono">
                                                {user.token.slice(0, 12)}...
                                            </code>
                                            <Button
                                                variant="ghost" size="icon" className="h-7 w-7"
                                                onClick={() => { navigator.clipboard.writeText(user.token); toast.success('Token copied'); }}
                                            >
                                                <Copy className="h-3 w-3" />
                                            </Button>
                                        </div>
                                    </TableCell>
                                    <TableCell className="text-sm text-muted-foreground">
                                        {new Date(user.created_at).toLocaleDateString('en-US')}
                                    </TableCell>
                                    <TableCell className="text-right">
                                        <div className="flex items-center justify-end gap-1">
                                            <Link href={`/users/${user.username}`}>
                                                <Button
                                                    variant="ghost" size="icon" className="h-8 w-8"
                                                    title="Edit User"
                                                >
                                                    <Pencil className="h-4 w-4" />
                                                </Button>
                                            </Link>
                                            <Button
                                                variant="ghost" size="icon" className="h-8 w-8"
                                                onClick={() => tokenMutation.mutate(user.username)}
                                                title="Regenerate Token"
                                            >
                                                <RefreshCw className="h-4 w-4" />
                                            </Button>
                                            <Dialog>
                                                <DialogTrigger asChild>
                                                    <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive">
                                                        <Trash2 className="h-4 w-4" />
                                                    </Button>
                                                </DialogTrigger>
                                                <DialogContent>
                                                    <DialogHeader>
                                                        <DialogTitle>Delete User</DialogTitle>
                                                    </DialogHeader>
                                                    <p className="text-sm text-muted-foreground py-4">
                                                        Are you sure you want to delete user <strong>{user.username}</strong>?
                                                    </p>
                                                    <DialogFooter>
                                                        <DialogClose asChild><Button variant="ghost">Cancel</Button></DialogClose>
                                                        <DialogClose asChild>
                                                            <Button variant="destructive" onClick={() => deleteMutation.mutate(user.username)}>
                                                                Delete
                                                            </Button>
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
                                    <TableCell colSpan={6} className="text-center text-muted-foreground py-12">
                                        {search ? 'No results found' : 'No users yet'}
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

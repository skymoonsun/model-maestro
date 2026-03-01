'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { systemConfigApi, type SystemConfigRaw } from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Separator } from '@/components/ui/separator';
import { toast } from 'sonner';
import { useState, useEffect } from 'react';
import { Save, Settings } from 'lucide-react';

export default function SettingsPage() {
    const queryClient = useQueryClient();
    const { data: config, isLoading } = useQuery({
        queryKey: ['system-config'],
        queryFn: systemConfigApi.get,
    });

    const [editValues, setEditValues] = useState<Record<string, Record<string, string>>>({});

    useEffect(() => {
        // eslint-disable-next-line react-hooks/set-state-in-effect
        if (config) setEditValues(JSON.parse(JSON.stringify(config)));
    }, [config]);

    const updateMutation = useMutation({
        mutationFn: (data: Record<string, string>) => systemConfigApi.update(data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['system-config'] });
            toast.success('Settings updated');
        },
        onError: (err) => toast.error(err.message),
    });

    const handleSave = () => {
        // Flatten to key-value
        const flat: Record<string, string> = {};
        Object.entries(editValues).forEach(([, group]) => {
            Object.entries(group).forEach(([key, value]) => {
                flat[key] = value;
            });
        });
        updateMutation.mutate(flat);
    };

    if (isLoading) return <Card><CardContent className="p-6"><Skeleton className="h-96 w-full" /></CardContent></Card>;

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-end">
                <Button size="sm" onClick={handleSave} disabled={updateMutation.isPending}>
                    <Save className="h-4 w-4 mr-2" />
                    {updateMutation.isPending ? 'Saving...' : 'Save All'}
                </Button>
            </div>

            {Object.entries(editValues).map(([category, values]) => (
                <Card key={category}>
                    <CardHeader className="pb-3">
                        <CardTitle className="text-sm flex items-center gap-2 capitalize">
                            <Settings className="h-4 w-4" />
                            {category.replace(/_/g, ' ')}
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            {Object.entries(values).map(([key, value]) => (
                                <div key={key}>
                                    <label className="text-xs text-muted-foreground font-mono">{key}</label>
                                    <Input
                                        value={typeof value === 'string' ? value : JSON.stringify(value)}
                                        onChange={(e) => setEditValues((prev) => ({
                                            ...prev,
                                            [category]: { ...prev[category], [key]: e.target.value },
                                        }))}
                                        className="font-mono text-sm"
                                    />
                                </div>
                            ))}
                        </div>
                    </CardContent>
                </Card>
            ))}
        </div>
    );
}

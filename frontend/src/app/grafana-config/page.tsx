'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { grafanaConfigApi } from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from 'sonner';
import { useState, useEffect } from 'react';
import { Save, BrainCircuit } from 'lucide-react';
import {
    Select,
    SelectContent,
    SelectGroup,
    SelectItem,
    SelectLabel,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select';

export default function GrafanaConfigPage() {
    const queryClient = useQueryClient();
    const { data: cfg, isLoading } = useQuery({
        queryKey: ['grafana-config'],
        queryFn: grafanaConfigApi.get,
    });

    const [selectedModel, setSelectedModel] = useState<string>('');

    useEffect(() => {
        if (cfg?.model) {
            setSelectedModel(cfg.model);
        }
    }, [cfg]);

    const updateMutation = useMutation({
        mutationFn: (model: string) => grafanaConfigApi.update({ model }),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['grafana-config'] });
            toast.success('Grafana model saved');
        },
        onError: (err: Error) => toast.error(err.message),
    });

    const handleSave = () => {
        if (!selectedModel || selectedModel === '__none__') return;
        updateMutation.mutate(selectedModel);
    };

    if (isLoading) {
        return (
            <Card>
                <CardContent className="p-6">
                    <Skeleton className="h-40 w-full" />
                </CardContent>
            </Card>
        );
    }

    const available = cfg?.available_models ?? [];
    console.log('[GrafanaConfig] available_models:', available);

    return (
        <div className="space-y-4 max-w-xl">
            <Card>
                <CardHeader className="pb-3">
                    <CardTitle className="text-base flex items-center gap-2">
                        <BrainCircuit className="h-4 w-4" />
                        Grafana Assistant Model
                    </CardTitle>
                    <CardDescription>
                        Choose the LLM model that Grafana Assistant will proxy through.
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="space-y-1.5">
                        <label className="text-sm font-medium text-muted-foreground">Model</label>
                        <Select value={selectedModel || '__none__'} onValueChange={(v) => setSelectedModel(v === '__none__' ? '' : v)}>
                            <SelectTrigger className="w-full">
                                <SelectValue placeholder="Select a model…" />
                            </SelectTrigger>
                            <SelectContent position="popper">
                                <SelectGroup>
                                    {available.length > 0 ? (
                                        <>
                                            <SelectLabel>Mapped Models</SelectLabel>
                                            {available.map((m) => (
                                                <SelectItem key={m} value={m}>
                                                    {m}
                                                </SelectItem>
                                            ))}
                                        </>
                                    ) : (
                                        <SelectItem value="__none__" disabled>
                                            No mapped models found — add model mappings in Settings
                                        </SelectItem>
                                    )}
                                </SelectGroup>
                            </SelectContent>
                        </Select>
                    </div>

                    <div className="flex items-center justify-end pt-2">
                        <Button size="sm" onClick={handleSave} disabled={updateMutation.isPending || !selectedModel || selectedModel === '__none__'}>
                            <Save className="h-4 w-4 mr-2" />
                            {updateMutation.isPending ? 'Saving…' : 'Save'}
                        </Button>
                    </div>
                </CardContent>
            </Card>
        </div>
    );
}

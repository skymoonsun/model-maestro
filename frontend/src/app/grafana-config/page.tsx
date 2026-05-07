'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { grafanaConfigApi } from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from 'sonner';
import { useState, useEffect } from 'react';
import { Save, BrainCircuit, Check } from 'lucide-react';
import { cn } from '@/lib/utils';
import {
    Command,
    CommandEmpty,
    CommandGroup,
    CommandInput,
    CommandItem,
    CommandList,
} from '@/components/ui/command';
import {
    Popover,
    PopoverContent,
    PopoverTrigger,
} from '@/components/ui/popover';

export default function GrafanaConfigPage() {
    const queryClient = useQueryClient();
    const { data: cfg, isLoading } = useQuery({
        queryKey: ['grafana-config'],
        queryFn: grafanaConfigApi.get,
    });

    const [selectedModel, setSelectedModel] = useState<string>('');
    const [open, setOpen] = useState(false);

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
        if (!selectedModel) return;
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

    const detailsMap = new Map(
        (cfg?.model_details ?? []).map((d) => [d.name, d])
    );
    const available = cfg?.available_models ?? [];

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
                        <Popover open={open} onOpenChange={setOpen}>
                            <PopoverTrigger asChild>
                                <Button
                                    variant="outline"
                                    role="combobox"
                                    aria-expanded={open}
                                    className="w-full justify-between"
                                >
                                    {selectedModel || 'Select a model…'}
                                </Button>
                            </PopoverTrigger>
                            <PopoverContent className="w-[400px] p-0" align="start">
                                <Command>
                                    <CommandInput placeholder="Search models…" />
                                    <CommandList>
                                        <CommandEmpty>No models found.</CommandEmpty>
                                        <CommandGroup>
                                            {available.map((m) => {
                                                const detail = detailsMap.get(m);
                                                const isMapped = detail?.is_mapped ?? false;
                                                const nodes = detail?.nodes ?? [];
                                                const nodeNames = nodes.map((n) => n.name).join(', ');
                                                return (
                                                    <CommandItem
                                                        key={m}
                                                        value={m}
                                                        onSelect={(currentValue) => {
                                                            setSelectedModel(currentValue === selectedModel ? '' : currentValue);
                                                            setOpen(false);
                                                        }}
                                                    >
                                                        <div className="flex items-start gap-2 w-full">
                                                            <Check
                                                                className={cn(
                                                                    'mt-0.5 h-4 w-4 shrink-0',
                                                                    selectedModel === m ? 'opacity-100' : 'opacity-0'
                                                                )}
                                                            />
                                                            <div className="flex-1 min-w-0">
                                                                <div className="flex items-center gap-2">
                                                                    <span className="font-medium truncate">
                                                                        {m}
                                                                    </span>
                                                                    {isMapped ? (
                                                                        <Badge variant="outline" className="text-emerald-400 border-emerald-400/30 bg-emerald-400/10 text-[10px] px-1 py-0">
                                                                            Mapped
                                                                        </Badge>
                                                                    ) : (
                                                                        <Badge variant="outline" className="text-amber-400 border-amber-400/30 bg-amber-400/10 text-[10px] px-1 py-0">
                                                                            Unmapped
                                                                        </Badge>
                                                                    )}
                                                                </div>
                                                                {nodes.length > 0 && (
                                                                    <div className="text-[11px] text-muted-foreground truncate mt-0.5">
                                                                        {nodeNames}
                                                                    </div>
                                                                )}
                                                            </div>
                                                        </div>
                                                    </CommandItem>
                                                );
                                            })}
                                        </CommandGroup>
                                    </CommandList>
                                </Command>
                            </PopoverContent>
                        </Popover>
                    </div>

                    <div className="flex items-center justify-end pt-2">
                        <Button size="sm" onClick={handleSave} disabled={updateMutation.isPending || !selectedModel}>
                            <Save className="h-4 w-4 mr-2" />
                            {updateMutation.isPending ? 'Saving…' : 'Save'}
                        </Button>
                    </div>
                </CardContent>
            </Card>
        </div>
    );
}

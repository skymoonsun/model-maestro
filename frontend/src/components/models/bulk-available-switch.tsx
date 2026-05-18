'use client';

import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Switch } from '@/components/ui/switch';
import { ollamaModelsApi } from '@/lib/api';
import { toast } from 'sonner';

export type ModelAvailabilityRow = {
    name: string;
    is_available: boolean;
};

type BulkAvailableSwitchProps = {
    /** Visible rows (respects search filter). */
    models: ModelAvailabilityRow[];
    invalidateQueryKeys: string[][];
};

export function BulkAvailableSwitch({ models, invalidateQueryKeys }: BulkAvailableSwitchProps) {
    const qc = useQueryClient();
    const [pending, setPending] = useState(false);

    const allAvailable = models.length > 0 && models.every((m) => m.is_available);

    const handleChange = async (isAvailable: boolean) => {
        if (models.length === 0) return;
        setPending(true);
        try {
            const results = await Promise.allSettled(
                models.map((m) => ollamaModelsApi.setAvailable(m.name, isAvailable))
            );
            const failed = results.filter((r) => r.status === 'rejected').length;
            for (const key of invalidateQueryKeys) {
                await qc.invalidateQueries({ queryKey: key });
            }
            if (failed > 0) {
                toast.error(`Updated ${models.length - failed} of ${models.length} models`);
            } else {
                toast.success(
                    isAvailable
                        ? `All ${models.length} visible model(s) are now available`
                        : `All ${models.length} visible model(s) are now unavailable`
                );
            }
        } catch (err) {
            toast.error(err instanceof Error ? err.message : 'Bulk update failed');
        } finally {
            setPending(false);
        }
    };

    return (
        <Switch
            checked={allAvailable}
            onCheckedChange={handleChange}
            disabled={pending || models.length === 0}
            aria-label="Toggle availability for all visible models"
        />
    );
}

'use client';

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import OllamaModelsPage from './ollama/page';
import VllmModelsPage from './vllm/page';

export default function ModelsPage() {
    return (
        <div className="space-y-4">
            <Tabs defaultValue="ollama" className="space-y-4">
                <TabsList>
                    <TabsTrigger value="ollama">Ollama Models</TabsTrigger>
                    <TabsTrigger value="vllm">vLLM Models</TabsTrigger>
                </TabsList>
                <TabsContent value="ollama" className="space-y-4">
                    <OllamaModelsPage />
                </TabsContent>
                <TabsContent value="vllm" className="space-y-4">
                    <VllmModelsPage />
                </TabsContent>
            </Tabs>
        </div>
    );
}

'use client';

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import OllamaModelsPage from './ollama/page';
import VllmModelsPage from './vllm/page';
import AntigravityModelsPage from './antigravity/page';
import BedrockModelsPage from './bedrock/page';

export default function ModelsPage() {
    return (
        <div className="space-y-4">
            <Tabs defaultValue="ollama" className="space-y-4">
                <TabsList>
                    <TabsTrigger value="ollama">Ollama Models</TabsTrigger>
                    <TabsTrigger value="vllm">vLLM Models</TabsTrigger>
                    <TabsTrigger value="antigravity">Antigravity Models</TabsTrigger>
                    <TabsTrigger value="bedrock">Bedrock Models</TabsTrigger>
                </TabsList>
                <TabsContent value="ollama" className="space-y-4">
                    <OllamaModelsPage />
                </TabsContent>
                <TabsContent value="vllm" className="space-y-4">
                    <VllmModelsPage />
                </TabsContent>
                <TabsContent value="antigravity" className="space-y-4">
                    <AntigravityModelsPage />
                </TabsContent>
                <TabsContent value="bedrock" className="space-y-4">
                    <BedrockModelsPage />
                </TabsContent>
            </Tabs>
        </div>
    );
}

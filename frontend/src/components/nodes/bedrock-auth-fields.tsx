'use client';

import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select';
import type { CreateNode } from '@/lib/api';

export type BedrockAuthMode = 'iam' | 'api_key';

export function inferBedrockAuthMode(node: {
    bedrock_auth_mode?: string | null;
    aws_secret_key?: string | null;
}): BedrockAuthMode {
    if (node.bedrock_auth_mode === 'api_key' || node.bedrock_auth_mode === 'iam') {
        return node.bedrock_auth_mode;
    }
    return node.aws_secret_key ? 'iam' : 'api_key';
}

export function isBedrockNodeFormValid(
    form: Pick<CreateNode, 'api_key' | 'aws_secret_key' | 'aws_region' | 'base_url'>,
    mode: BedrockAuthMode,
): boolean {
    if (!form.aws_region && !form.base_url) return false;
    if (mode === 'iam') {
        return Boolean(form.api_key?.trim() && form.aws_secret_key?.trim());
    }
    return Boolean(form.api_key?.trim());
}

type BedrockAuthFieldsProps = {
    form: CreateNode & { bedrock_auth_mode?: BedrockAuthMode };
    setForm: React.Dispatch<React.SetStateAction<CreateNode & { bedrock_auth_mode?: BedrockAuthMode }>>;
    authMode: BedrockAuthMode;
    onAuthModeChange: (mode: BedrockAuthMode) => void;
};

export function BedrockAuthFields({ form, setForm, authMode, onAuthModeChange }: BedrockAuthFieldsProps) {
    return (
        <>
            <div>
                <Label>Authentication</Label>
                <Select
                    value={authMode}
                    onValueChange={(v) => onAuthModeChange(v as BedrockAuthMode)}
                >
                    <SelectTrigger>
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="iam">IAM access key + secret</SelectItem>
                        <SelectItem value="api_key">Bedrock API key</SelectItem>
                    </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground mt-1">
                    Bedrock API keys work like AnythingLLM: only the API key and region are required.
                </p>
            </div>
            {authMode === 'iam' ? (
                <>
                    <div>
                        <Label>AWS Access Key ID</Label>
                        <Input
                            placeholder="AKIA..."
                            value={form.api_key || ''}
                            onChange={(e) => setForm((f) => ({ ...f, api_key: e.target.value || undefined }))}
                        />
                    </div>
                    <div>
                        <Label>AWS Secret Access Key</Label>
                        <Input
                            type="password"
                            placeholder="Secret access key"
                            value={form.aws_secret_key || ''}
                            onChange={(e) =>
                                setForm((f) => ({ ...f, aws_secret_key: e.target.value || undefined }))
                            }
                        />
                    </div>
                    <div>
                        <Label>AWS Session Token (optional)</Label>
                        <Input
                            placeholder="STS session token"
                            value={form.aws_session_token || ''}
                            onChange={(e) =>
                                setForm((f) => ({ ...f, aws_session_token: e.target.value || undefined }))
                            }
                        />
                    </div>
                </>
            ) : (
                <div>
                    <Label>Bedrock API Key</Label>
                    <Input
                        type="password"
                        placeholder="Bedrock API key from AWS console"
                        value={form.api_key || ''}
                        onChange={(e) => setForm((f) => ({ ...f, api_key: e.target.value || undefined }))}
                    />
                </div>
            )}
            <div>
                <Label>AWS Region</Label>
                <Input
                    placeholder="us-east-1"
                    value={form.aws_region || ''}
                    onChange={(e) => setForm((f) => ({ ...f, aws_region: e.target.value || undefined }))}
                />
                <p className="text-xs text-muted-foreground mt-1">
                    Base URL defaults to https://bedrock-runtime.{'{region}'}.amazonaws.com when empty.
                </p>
            </div>
        </>
    );
}

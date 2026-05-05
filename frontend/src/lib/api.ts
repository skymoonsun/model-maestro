// Browser requests go through Next.js rewrite proxy: /api/proxy/* -> backend
const API_BASE = typeof window !== 'undefined'
  ? '/api/proxy'  // Browser: use Next.js rewrite proxy
  : (process.env.BACKEND_URL || 'http://localhost:8000');  // Server-side

const TOKEN_STORAGE_KEY = 'admin_token';

function getAdminToken(): string {
  if (typeof window === 'undefined') return '';
  return sessionStorage.getItem(TOKEN_STORAGE_KEY) || '';
}

export function clearAdminToken(): void {
  if (typeof window !== 'undefined') sessionStorage.removeItem(TOKEN_STORAGE_KEY);
}

async function handleUnauthorized() {
  if (typeof window !== 'undefined') {
    clearAdminToken();
    try { await fetch('/api/auth/logout', { method: 'POST' }); } catch { }
    window.location.href = '/login';
  }
}

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export async function adminFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const token = getAdminToken();
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  });
  if (!res.ok) {
    if (res.status === 401 && typeof window !== 'undefined') {
      await handleUnauthorized();
    }
    const text = await res.text();
    throw new ApiError(text || res.statusText, res.status);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return res.json();
}

export async function adminFetchText(path: string, options?: RequestInit): Promise<string> {
  const token = getAdminToken();
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  });
  if (!res.ok) {
    if (res.status === 401 && typeof window !== 'undefined') {
      await handleUnauthorized();
    }
    throw new ApiError(await res.text(), res.status);
  }
  return res.text();
}

export async function streamFetch(
  path: string,
  body: unknown,
  onProgress: (data: Record<string, unknown>) => void
) {
  const token = getAdminToken();
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    if (res.status === 401 && typeof window !== 'undefined') {
      await handleUnauthorized();
    }
    throw new ApiError(await res.text(), res.status);
  }

  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';
    for (const line of lines) {
      if (line.trim()) {
        try { onProgress(JSON.parse(line)); } catch { }
      }
    }
  }
}

// ==================== Dashboard ====================
export const dashboardApi = {
  getStats: () => adminFetch<DashboardStats>('/admin/dashboard/stats'),
  getRequestsChart: async (period = '7d') => {
    const res = await adminFetch<{ labels: string[], data: number[] }>(`/admin/dashboard/charts/requests?period=${period}`);
    return res.labels.map((label, i) => ({ date: label, count: res.data[i] }));
  },
  getTokensChart: async (period = '7d') => {
    const res = await adminFetch<{ labels: string[], data: number[] }>(`/admin/dashboard/charts/tokens?period=${period}`);
    return res.labels.map((label, i) => ({ date: label, count: res.data[i] }));
  },
  getModelsChart: async (period = '7d') => {
    const res = await adminFetch<{ labels: string[], data: number[] }>(`/admin/dashboard/charts/models?period=${period}`);
    return res.labels.map((label, i) => ({ model: label, count: res.data[i] }));
  },
  getRequestLog: (params?: {
    limit?: number; offset?: number; username?: string;
    model_name?: string; status_code?: number; status_category?: string;
    request_type?: string; start_date?: string; end_date?: string;
  }) => {
    const query = new URLSearchParams();
    if (params?.limit) query.set('limit', String(params.limit));
    if (params?.offset) query.set('offset', String(params.offset));
    if (params?.username) query.set('username', params.username);
    if (params?.model_name) query.set('model_name', params.model_name);
    if (params?.status_code !== undefined) query.set('status_code', String(params.status_code));
    if (params?.status_category) query.set('status_category', params.status_category);
    if (params?.request_type) query.set('request_type', params.request_type);
    if (params?.start_date) query.set('start_date', params.start_date);
    if (params?.end_date) query.set('end_date', params.end_date);
    return adminFetch<RequestLogResponse>(`/admin/dashboard/requests-log?${query.toString()}`);
  },
  getUserStats: (params?: { start_date?: string; end_date?: string }) => {
    const query = new URLSearchParams();
    if (params?.start_date) query.set('start_date', params.start_date);
    if (params?.end_date) query.set('end_date', params.end_date);
    return adminFetch<UserStatsResponse>(`/admin/dashboard/user-stats?${query.toString()}`);
  },
};

// ==================== Users ====================
export const usersApi = {
  list: () => adminFetch<User[]>('/admin/users'),
  get: (username: string) => adminFetch<User>(`/admin/users/${username}`),
  create: (username: string) =>
    adminFetch<User>('/admin/users', { method: 'POST', body: JSON.stringify({ username }) }),
  delete: (username: string) =>
    adminFetch<void>(`/admin/users/${username}`, { method: 'DELETE' }),
  regenerateToken: (username: string) =>
    adminFetch<User>(`/admin/users/${username}/token`, { method: 'PUT' }),
  // Models
  getModels: (username: string) =>
    adminFetch<UserModels>(`/admin/users/${username}/models`),
  setModels: (username: string, models: string[]) =>
    adminFetch<UserModels>(`/admin/users/${username}/models`, {
      method: 'POST', body: JSON.stringify({ models }),
    }),
  setAllModels: (username: string) =>
    adminFetch<UserModels>(`/admin/users/${username}/models/all`, { method: 'POST' }),
  removeModel: (username: string, model: string) =>
    adminFetch<void>(`/admin/users/${username}/models/${encodeURIComponent(model)}`, { method: 'DELETE' }),
  // Limits
  getLimits: (username: string) =>
    adminFetch<UserLimits>(`/admin/users/${username}/limits`),
  setLimits: (username: string, limits: { request_limit?: number; token_limit?: number }) =>
    adminFetch<UserLimits>(`/admin/users/${username}/limits`, {
      method: 'POST', body: JSON.stringify(limits),
    }),
  removeLimits: (username: string) =>
    adminFetch<void>(`/admin/users/${username}/limits`, { method: 'DELETE' }),
  // Activity
  getActivity: (username: string, limit = 50, offset = 0) =>
    adminFetch<ActivityLog[]>(`/admin/users/${username}/activity?limit=${limit}&offset=${offset}`),
  getTokenUsage: (username: string, period = '7d') =>
    adminFetch<TokenUsage[]>(`/admin/users/${username}/token-usage?period=${period}`),
  getModelUsage: (username: string, period = '7d') =>
    adminFetch<ModelUsage[]>(`/admin/users/${username}/model-usage?period=${period}`),
};

// ==================== Model Mappings ====================
export const modelMappingsApi = {
  list: () => adminFetch<ModelMapping[]>('/admin/model-mappings'),
  create: (data: CreateModelMapping) =>
    adminFetch<ModelMapping>('/admin/model-mappings', {
      method: 'POST', body: JSON.stringify(data),
    }),
  delete: (displayName: string) =>
    adminFetch<void>(`/admin/model-mappings/${encodeURIComponent(displayName)}`, { method: 'DELETE' }),
  invalidateCache: () =>
    adminFetch<void>('/admin/model-mappings/invalidate-cache', { method: 'POST' }),
};

// ==================== Ollama Models ====================
export const ollamaModelsApi = {
  list: () => adminFetch<OllamaModel[]>('/admin/models/ollama'),
  show: (name: string) =>
    adminFetch<Record<string, unknown>>(`/admin/models/show?name=${encodeURIComponent(name)}`, { method: 'POST' }),
  pull: (name: string, onProgress: (data: Record<string, unknown>) => void) =>
    streamFetch('/admin/models/pull', { name, stream: true }, onProgress),
  delete: (name: string) =>
    adminFetch<void>(`/admin/models/ollama/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  syncCapabilities: () =>
    adminFetch<SyncResult>('/admin/models/sync-capabilities', { method: 'POST' }),
  updateCapabilities: (displayName: string, capabilities: string[]) =>
    adminFetch<ModelMapping>(`/admin/models/${encodeURIComponent(displayName)}/capabilities`, {
      method: 'PATCH', body: JSON.stringify({ capabilities }),
    }),
};

// ==================== vLLM Models ====================
export const vllmModelsApi = {
  list: () => adminFetch<VllmModel[]>('/admin/models/vllm'),
};

// ==================== Model Config ====================
export const modelConfigApi = {
  list: () => adminFetch<ModelConfig[]>('/admin/model-config'),
  create: (data: CreateModelConfig) =>
    adminFetch<ModelConfig>('/admin/model-config', { method: 'POST', body: JSON.stringify(data) }),
  update: (id: number, data: Partial<CreateModelConfig>) =>
    adminFetch<ModelConfig>(`/admin/model-config/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  delete: (id: number) =>
    adminFetch<void>(`/admin/model-config/${id}`, { method: 'DELETE' }),
};

// ==================== Tool Sets ====================
export const toolSetsApi = {
  list: () => adminFetch<ToolSet[]>('/admin/tool-sets'),
  create: (data: CreateToolSet) =>
    adminFetch<ToolSet>('/admin/tool-sets', { method: 'POST', body: JSON.stringify(data) }),
  update: (id: number, data: Partial<CreateToolSet>) =>
    adminFetch<ToolSet>(`/admin/tool-sets/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  delete: (id: number) =>
    adminFetch<void>(`/admin/tool-sets/${id}`, { method: 'DELETE' }),
};

// ==================== System Config ====================
export const systemConfigApi = {
  get: () => adminFetch<Record<string, Record<string, string>>>('/admin/config'),
  getRaw: () => adminFetch<SystemConfigRaw[]>('/admin/config/raw'),
  update: (data: Record<string, string>) =>
    adminFetch<void>('/admin/config', { method: 'PUT', body: JSON.stringify(data) }),
};

// ==================== Grafana Config ====================
export interface GrafanaConfigResponse {
  model: string;
  available_models: string[];
}

export interface GrafanaConfigUpdate {
  model: string;
  api_base_url?: string;
}

export const grafanaConfigApi = {
  get: () => adminFetch<GrafanaConfigResponse>('/admin/grafana/config'),
  update: (data: GrafanaConfigUpdate) =>
    adminFetch<void>('/admin/grafana/config', { method: 'PUT', body: JSON.stringify(data) }),
};

// ==================== Nodes (Load Balancing) ====================
export const nodesApi = {
  list: (activeOnly?: boolean) =>
    adminFetch<Node[]>(`/admin/nodes${activeOnly ? '?active_only=true' : ''}`),
  get: (id: number) => adminFetch<NodeDetail>(`/admin/nodes/${id}`),
  create: (data: CreateNode) =>
    adminFetch<OllamaNodeResponse>('/admin/nodes', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  update: (id: number, data: Partial<CreateNode>) =>
    adminFetch<OllamaNodeResponse>(`/admin/nodes/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  delete: (id: number) =>
    adminFetch<{ success: boolean; message: string }>(`/admin/nodes/${id}`, {
      method: 'DELETE',
    }),
  healthCheck: (id: number) =>
    adminFetch<HealthCheckResult>(`/admin/nodes/${id}/health-check`, {
      method: 'POST',
    }),
  syncModels: (id: number) =>
    adminFetch<NodeSyncResponse>(`/admin/nodes/${id}/sync-models`, {
      method: 'POST',
    }),
  syncAll: () =>
    adminFetch<NodeSyncResponse[]>('/admin/nodes/sync-all', {
      method: 'POST',
    }),
  getDistribution: () =>
    adminFetch<ModelDistribution[]>(`/admin/nodes/models/distribution`),
  getLoadStatus: () =>
    adminFetch<LoadBalancerStatus[]>('/admin/nodes/load-balancer/status'),
  pullModel: (nodeId: number, name: string, onProgress: (data: Record<string, unknown>) => void) =>
    streamFetch(`/admin/nodes/${nodeId}/pull-model`, { name, stream: true }, onProgress),
  pullModelAll: (name: string, onProgress: (data: Record<string, unknown>) => void) =>
    streamFetch('/admin/nodes/pull-model-all', { name, stream: true }, onProgress),
  getMetrics: (nodeId: number) =>
    adminFetch<NodeMetrics>(`/admin/nodes/${nodeId}/metrics`),
};

// ==================== Model Groups ====================
export const modelGroupsApi = {
  list: () => adminFetch<ModelGroupListResponse>('/admin/model-groups'),
  get: (name: string) => adminFetch<ModelGroupDetail>(`/admin/model-groups/${encodeURIComponent(name)}`),
  create: (data: ModelGroupCreate) =>
    adminFetch<ModelGroupDetail>('/admin/model-groups', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  update: (name: string, data: Partial<ModelGroupCreate>) =>
    adminFetch<ModelGroupDetail>(`/admin/model-groups/${encodeURIComponent(name)}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  delete: (name: string) =>
    adminFetch<void>(`/admin/model-groups/${encodeURIComponent(name)}`, {
      method: 'DELETE',
    }),
  addMember: (name: string, data: ModelGroupMemberCreate) =>
    adminFetch<ModelGroupMember>(`/admin/model-groups/${encodeURIComponent(name)}/members`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  removeMember: (name: string, memberId: number) =>
    adminFetch<void>(`/admin/model-groups/${encodeURIComponent(name)}/members/${memberId}`, {
      method: 'DELETE',
    }),
  reorderMembers: (name: string, members: { id: number; priority: number }[]) =>
    adminFetch<ModelGroupDetail>(`/admin/model-groups/${encodeURIComponent(name)}/members/reorder`, {
      method: 'PUT',
      body: JSON.stringify({ members }),
    }),
};

// ==================== Audit Logs ====================
export const auditLogsApi = {
  list: async (params?: { limit?: number; offset?: number; action?: string }) => {
    const query = new URLSearchParams();
    if (params?.limit) query.set('limit', String(params.limit));
    if (params?.offset) query.set('offset', String(params.offset));
    if (params?.action) query.set('action', params.action);
    const res = await adminFetch<{ logs: Record<string, unknown>[] }>(`/admin/audit-logs?${query.toString()}`);
    return res.logs.map(log => ({
      id: log.id,
      admin_action: log.action,
      target_type: log.entity_type,
      target_id: log.entity_id,
      details: log.details,
      created_at: log.created_at,
    })) as AuditLog[];
  },
};

// ==================== Types ====================
export interface DashboardStats {
  users: {
    total: number;
    active_today: number;
    new_this_week: number;
  };
  requests: {
    today: number;
    today_success: number;
    today_errors: number;
    today_avg_duration: number;
    this_week: number;
    this_month: number;
    total: number;
    total_avg_duration: number;
  };
  tokens: {
    today: {
      total: number;
      prompt: number;
      completion: number;
    };
    this_week: {
      total: number;
      prompt: number;
      completion: number;
    };
    this_month: {
      total: number;
      prompt: number;
      completion: number;
    };
    all_time: {
      total: number;
      prompt: number;
      completion: number;
    };
  };
  models: {
    most_used: Record<string, unknown>[];
    total_models: number;
  };
  system: {
    redis: string;
    postgres: string;
    ollama: string;
    queue_pending: number;
  };
}

export interface ChartData {
  date: string;
  count: number;
}

export interface ModelChartData {
  model: string;
  count: number;
}

export interface User {
  username: string;
  token: string;
  created_at: string;
  is_active: boolean;
  has_all_models?: boolean;
  models?: string[];
}

export interface UserModels {
  username: string;
  has_all_models: boolean;
  models: string[];
}

export interface UserLimits {
  username: string;
  request_limit: number | null;
  token_limit: number | null;
  created_at: string;
}

export interface ActivityLog {
  id: number;
  model_name: string;
  request_type: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  status_code: number | null;
  duration_ms: number | null;
  error_message: string | null;
  created_at: string;
}

export interface TokenUsage {
  date: string;
  total_tokens: number;
  request_count: number;
}

export interface ModelUsage {
  model: string;
  total_tokens: number;
  request_count: number;
}

export interface RequestLogResponse {
  logs: RequestLogItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface RequestLogItem {
  id: number;
  username: string | null;
  model_name: string;
  request_type: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  status_code: number | null;
  duration_ms: number | null;
  error_message: string | null;
  created_at: string | null;
}

export interface UserStatsItem {
  username: string;
  total_requests: number;
  total_tokens: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
}

export interface UserStatsResponse {
  users: UserStatsItem[];
  total_users: number;
}

export interface ModelMapping {
  display_name: string;
  real_name: string;
  node_id: number | null;
  node_name: string | null;
  context_length: number;
  context_length_display: string;
  capabilities: string[];
  created_at: string;
}

export interface CreateModelMapping {
  display_name: string;
  real_name: string;
  node_id?: number | null;
  context_length: string;
  capabilities?: string[];
}

export interface OllamaModel {
  name: string;
  size: number;
  digest: string;
  modified_at: string;
  details?: Record<string, unknown>;
  is_mapped: boolean;
  display_name: string | null;
  nodes?: string[] | null;
}

export interface VllmModel {
  name: string;
  node_name: string;
  node_id: number;
  base_url: string;
  model_size: number | null;
  model_family: string | null;
  digest: string | null;
  modified_at: string | null;
  is_mapped: boolean;
  display_name: string | null;
}

export interface SyncResult {
  synced: number;
  failed: number;
  results: Array<{
    display_name: string;
    real_name: string;
    capabilities: string[];
    status: string;
  }>;
}

export interface ModelConfig {
  id: number;
  model_prefix: string;
  is_exact_match: boolean;
  allowed_tools: string[] | null;
  unsupported_params: string[] | null;
  default_context_length: number | null;
  max_context_length: number | null;
  requests_per_minute: number | null;
  tokens_per_minute: number | null;
  is_active: boolean;
  maintenance_mode: boolean;
  description: string | null;
  cost_multiplier: number;
  created_at: string;
  updated_at: string | null;
}

export interface CreateModelConfig {
  model_prefix: string;
  is_exact_match?: boolean;
  allowed_tools?: string[] | null;
  unsupported_params?: string[] | null;
  default_context_length?: number | null;
  max_context_length?: number | null;
  requests_per_minute?: number | null;
  tokens_per_minute?: number | null;
  is_active?: boolean;
  maintenance_mode?: boolean;
  description?: string | null;
  cost_multiplier?: number;
}

export interface ToolSet {
  id: number;
  name: string;
  description: string | null;
  tools: string[] | null;
  is_active: boolean;
  created_at: string;
  updated_at: string | null;
}

export interface CreateToolSet {
  name: string;
  description?: string | null;
  tools: string[] | null;
  is_active?: boolean;
}

export interface SystemConfigRaw {
  key: string;
  value: string;
  category: string;
  description: string | null;
}

export interface AuditLog {
  id: number;
  admin_action: string;
  target_type: string;
  target_id: string;
  details: Record<string, unknown> | null;
  created_at: string;
}

// Node / Load Balancing types
export interface OllamaNodeResponse {
  id: number;
  name: string;
  base_url: string;
  api_key_set: boolean;
  priority: number;
  weight: number;
  is_active: boolean;
  node_type: string;
  health_status: string;
  last_health_check: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface NodeModel {
  model_name: string;
  model_size: number;
  model_family?: string | null;
  is_available: boolean;
}

export interface Node extends OllamaNodeResponse {
  model_count: number;
  models: NodeModel[];
}

export interface NodeDetail extends Node { }

export interface CreateNode {
  name: string;
  base_url: string;
  api_key?: string | null;
  priority?: number;
  weight?: number;
  is_active?: boolean;
  node_type?: string;
  health_check_url?: string | null;
}

export interface HealthCheckResult {
  node_id: number;
  node_name: string;
  health_status: string;
  error: string | null;
  checked_at: string;
}

export interface NodeSyncResponse {
  success: boolean;
  node_id: number;
  node_name: string;
  synced_count: number;
  models: string[];
  total_models: number;
  error?: string;
}

export interface ModelDistribution {
  model_name: string;
  node_count: number;
  nodes: string[];
}

export interface LoadBalancerStatus {
  id: number;
  name: string;
  base_url: string;
  health_status: string;
  is_active: boolean;
  model_count: number;
  load?: Record<string, unknown>;
}

export interface NodeMetrics {
  node_id: number;
  active_requests: number;
  total_requests_today: number;
  avg_response_time_ms: number | null;
}

// ==================== Model Group Types ====================
export interface ModelGroupMember {
  id: number;
  model_display_name: string;
  capability_tags: string[] | null;
  weight: number;
  priority: number;
  is_active: boolean;
  preferred_node_id: number | null;
}

export interface ModelGroupSummary {
  id: number;
  name: string;
  description: string | null;
  strategy: string;
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface ModelGroupDetail extends ModelGroupSummary {
  members: ModelGroupMember[];
}

export interface ModelGroupListResponse {
  groups: ModelGroupSummary[];
  total: number;
}

export type ModelGroupCreate = {
  name: string;
  description?: string | null;
  strategy?: string;
  is_active?: boolean;
  members?: ModelGroupMemberCreate[];
};

export type ModelGroupMemberCreate = {
  model_display_name: string;
  capability_tags?: string[] | null;
  weight?: number;
  priority?: number;
  is_active?: boolean;
  preferred_node_id?: number | null;
};

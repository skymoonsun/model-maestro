'use client';

import { useQuery } from '@tanstack/react-query';
import { dashboardApi, type DashboardStats, type ChartData, type ModelChartData, type UserStatsItem } from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Users, Activity, Key, Bot, CheckCircle, XCircle, Layers, Clock, Zap, ArrowDown, ArrowUp } from 'lucide-react';
import {
  AreaChart, Area, XAxis, YAxis, Tooltip as ReTooltip,
  ResponsiveContainer, BarChart, Bar, PieChart, Pie, Cell
} from 'recharts';

const COLORS = ['#6366f1', '#8b5cf6', '#a78bfa', '#c4b5fd', '#818cf8', '#7c3aed', '#4f46e5', '#4338ca'];

function formatNumber(value: number): string {
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return value.toLocaleString('tr-TR');
}

function formatDuration(ms: number | null): string {
  if (ms === null || ms === 0) return '-';
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms)}ms`;
}

function StatsCards({ stats }: { stats: DashboardStats }) {
  const tokensToday = stats.tokens?.today?.total ?? (stats.tokens?.today as any) ?? 0;
  const promptToday = stats.tokens?.today?.prompt ?? 0;
  const completionToday = stats.tokens?.today?.completion ?? 0;
  const tokensAllTime = stats.tokens?.all_time?.total ?? 0;
  const promptAllTime = stats.tokens?.all_time?.prompt ?? 0;
  const completionAllTime = stats.tokens?.all_time?.completion ?? 0;
  const reqSuccess = stats.requests?.today_success ?? 0;
  const reqErrors = stats.requests?.today_errors ?? 0;
  const reqTotal = stats.requests?.total ?? 0;
  const avgDuration = stats.requests?.today_avg_duration ?? 0;
  const totalAvgDuration = stats.requests?.total_avg_duration ?? 0;

  const items = [
    {
      label: 'Total Users',
      value: stats.users?.total || 0,
      sub: `${stats.users?.active_today || 0} active today`,
      icon: Users,
      gradient: 'from-blue-500/20 to-blue-600/5',
      iconColor: 'text-blue-400',
    },
    {
      label: 'Requests',
      value: (stats.requests?.today || 0).toLocaleString('tr-TR'),
      sub: `${reqSuccess} success · ${reqErrors} errors · ${formatNumber(reqTotal)} total`,
      icon: Activity,
      gradient: 'from-emerald-500/20 to-emerald-600/5',
      iconColor: 'text-emerald-400',
    },
    {
      label: 'Avg Response',
      value: formatDuration(avgDuration),
      sub: totalAvgDuration > 0 ? `${formatDuration(totalAvgDuration)} all-time avg` : 'today only',
      icon: Clock,
      gradient: 'from-orange-500/20 to-orange-600/5',
      iconColor: 'text-orange-400',
    },
    {
      label: 'Tokens',
      value: formatNumber(tokensToday),
      sub: `${formatNumber(promptToday)} prompt · ${formatNumber(completionToday)} completion · ${formatNumber(tokensAllTime)} total`,
      icon: Zap,
      gradient: 'from-violet-500/20 to-violet-600/5',
      iconColor: 'text-violet-400',
    },
  ];

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      {items.map((item) => (
        <Card key={item.label} className="relative overflow-hidden">
          <div className={`absolute inset-0 bg-gradient-to-br ${item.gradient}`} />
          <CardContent className="relative p-5">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-muted-foreground">{item.label}</p>
                <p className="text-2xl font-bold mt-1">{item.value}</p>
                {item.sub && <p className="text-xs text-muted-foreground mt-1">{item.sub}</p>}
              </div>
              <div className={`p-3 rounded-xl bg-background/50 ${item.iconColor}`}>
                <item.icon className="h-5 w-5" />
              </div>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function SystemStatus({ status }: { status: DashboardStats['system'] }) {
  const services = [
    { name: 'Redis', status: status.redis },
    { name: 'PostgreSQL', status: status.postgres },
    { name: 'Ollama', status: status.ollama },
  ];

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-medium">System Status</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {services.map((svc) => (
          <div key={svc.name} className="flex items-center justify-between">
            <span className="text-sm">{svc.name}</span>
            {svc.status === 'connected' || svc.status === 'ok' ? (
              <Badge variant="outline" className="text-emerald-400 border-emerald-400/30 bg-emerald-400/10">
                <CheckCircle className="h-3 w-3 mr-1" /> Connected
              </Badge>
            ) : (
              <Badge variant="outline" className="text-red-400 border-red-400/30 bg-red-400/10">
                <XCircle className="h-3 w-3 mr-1" /> Error
              </Badge>
            )}
          </div>
        ))}
        <div className="flex items-center justify-between pt-1 border-t border-border">
          <span className="text-sm">Queue</span>
          <Badge variant="outline" className="text-muted-foreground">
            <Layers className="h-3 w-3 mr-1" /> {status.queue_pending} pending
          </Badge>
        </div>
      </CardContent>
    </Card>
  );
}

function RequestsChart({ data }: { data: ChartData[] }) {
  return (
    <Card className="lg:col-span-2">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Requests (Last 7 Days)</CardTitle>
      </CardHeader>
      <CardContent>
        <ResponsiveContainer width="100%" height={240}>
          <AreaChart data={data}>
            <defs>
              <linearGradient id="areaGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#6366f1" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
              </linearGradient>
            </defs>
            <XAxis dataKey="date" stroke="#71717a" fontSize={12} tickLine={false} axisLine={false} />
            <YAxis stroke="#71717a" fontSize={12} tickLine={false} axisLine={false} />
            <ReTooltip
              contentStyle={{ backgroundColor: '#18181b', border: '1px solid #27272a', borderRadius: '8px' }}
              labelStyle={{ color: '#a1a1aa' }}
            />
            <Area type="monotone" dataKey="count" stroke="#6366f1" fill="url(#areaGradient)" strokeWidth={2} />
          </AreaChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}

function TokensChart({ data }: { data: ChartData[] }) {
  return (
    <Card className="lg:col-span-2">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Token Usage (Last 7 Days)</CardTitle>
      </CardHeader>
      <CardContent>
        <ResponsiveContainer width="100%" height={240}>
          <AreaChart data={data}>
            <defs>
              <linearGradient id="tokenGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#10b981" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
              </linearGradient>
            </defs>
            <XAxis dataKey="date" stroke="#71717a" fontSize={12} tickLine={false} axisLine={false} />
            <YAxis stroke="#71717a" fontSize={12} tickLine={false} axisLine={false} tickFormatter={(v: number) => formatNumber(v)} />
            <ReTooltip
              contentStyle={{ backgroundColor: '#18181b', border: '1px solid #27272a', borderRadius: '8px' }}
              labelStyle={{ color: '#a1a1aa' }}
              formatter={(value: number) => [formatNumber(value), 'Tokens']}
            />
            <Area type="monotone" dataKey="count" stroke="#10b981" fill="url(#tokenGradient)" strokeWidth={2} />
          </AreaChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}

function ModelsChart({ data }: { data: ModelChartData[] }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Most Used Models</CardTitle>
      </CardHeader>
      <CardContent>
        {data.length > 0 ? (
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={data.slice(0, 6)} layout="vertical">
              <XAxis type="number" stroke="#71717a" fontSize={12} tickLine={false} axisLine={false} />
              <YAxis
                type="category" dataKey="model" stroke="#71717a" fontSize={11}
                tickLine={false} axisLine={false} width={120}
              />
              <ReTooltip
                contentStyle={{ backgroundColor: '#18181b', border: '1px solid #27272a', borderRadius: '8px' }}
              />
              <Bar dataKey="count" radius={[0, 4, 4, 0]}>
                {data.slice(0, 6).map((_, i) => (
                  <Cell key={i} fill={COLORS[i % COLORS.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <p className="text-sm text-muted-foreground text-center py-12">No data yet</p>
        )}
      </CardContent>
    </Card>
  );
}

function UserStatsTable({ users }: { users: UserStatsItem[] }) {
  if (!users || users.length === 0) {
    return null;
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Top Users by Token Usage</CardTitle>
      </CardHeader>
      <CardContent className="p-0 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border">
              <th className="text-left font-medium text-muted-foreground px-4 py-2">User</th>
              <th className="text-right font-medium text-muted-foreground px-4 py-2">Requests</th>
              <th className="text-right font-medium text-muted-foreground px-4 py-2">Prompt Tokens</th>
              <th className="text-right font-medium text-muted-foreground px-4 py-2">Completion Tokens</th>
              <th className="text-right font-medium text-muted-foreground px-4 py-2">Total Tokens</th>
            </tr>
          </thead>
          <tbody>
            {users.slice(0, 10).map((user) => (
              <tr key={user.username} className="border-b border-border/50 hover:bg-accent/50">
                <td className="px-4 py-2 font-mono text-xs">{user.username}</td>
                <td className="px-4 py-2 text-right text-xs">{user.total_requests.toLocaleString()}</td>
                <td className="px-4 py-2 text-right text-xs">{formatNumber(user.total_prompt_tokens)}</td>
                <td className="px-4 py-2 text-right text-xs">{formatNumber(user.total_completion_tokens)}</td>
                <td className="px-4 py-2 text-right text-xs font-medium">{formatNumber(user.total_tokens)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}

function LoadingSkeleton() {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {[...Array(4)].map((_, i) => (
          <Card key={i}><CardContent className="p-5"><Skeleton className="h-16 w-full" /></CardContent></Card>
        ))}
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card className="lg:col-span-2"><CardContent className="p-5"><Skeleton className="h-60 w-full" /></CardContent></Card>
        <Card><CardContent className="p-5"><Skeleton className="h-60 w-full" /></CardContent></Card>
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const { data: stats, isLoading: statsLoading, isError: statsError } = useQuery({
    queryKey: ['dashboard', 'stats'],
    queryFn: dashboardApi.getStats,
  });

  const { data: requestsChart } = useQuery({
    queryKey: ['dashboard', 'requests'],
    queryFn: () => dashboardApi.getRequestsChart(),
  });

  const { data: tokensChart } = useQuery({
    queryKey: ['dashboard', 'tokens'],
    queryFn: () => dashboardApi.getTokensChart(),
  });

  const { data: modelsChart } = useQuery({
    queryKey: ['dashboard', 'models'],
    queryFn: () => dashboardApi.getModelsChart(),
  });

  const { data: userStats } = useQuery({
    queryKey: ['dashboard', 'user-stats'],
    queryFn: () => dashboardApi.getUserStats(),
  });

  if (statsError) {
    return (
      <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-6 text-center space-y-3">
        <p className="font-medium">Dashboard yüklenemedi</p>
        <p className="text-sm text-muted-foreground">
          Backend&apos;e ulaşılamıyor olabilir veya oturum geçersiz. Giriş sayfasını deneyin.
        </p>
        <a
          href="/login"
          className="inline-block text-sm text-primary underline underline-offset-4"
        >
          Giriş sayfasına git
        </a>
      </div>
    );
  }

  if (statsLoading || !stats) return <LoadingSkeleton />;

  return (
    <div className="space-y-6">
      <StatsCards stats={stats} />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <RequestsChart data={requestsChart || []} />
        <SystemStatus status={stats.system} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <TokensChart data={tokensChart || []} />
        <ModelsChart data={modelsChart || []} />
      </div>

      {userStats?.users && userStats.users.length > 0 && (
        <UserStatsTable users={userStats.users} />
      )}
    </div>
  );
}
'use client';

import { useQuery } from '@tanstack/react-query';
import { dashboardApi, type RequestLogItem } from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { useState } from 'react';
import { ChevronLeft, ChevronRight, FileText } from 'lucide-react';

function formatNumber(value: number): string {
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return value.toLocaleString();
}

function formatDuration(ms: number | null): string {
  if (ms === null || ms === undefined) return '-';
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${ms}ms`;
}

function StatusBadge({ statusCode }: { statusCode: number | null }) {
  if (statusCode === null || statusCode === undefined) {
    return <Badge variant="outline" className="text-muted-foreground">-</Badge>;
  }
  if (statusCode >= 200 && statusCode < 300) {
    return <Badge variant="outline" className="text-emerald-400 border-emerald-400/30 bg-emerald-400/10">{statusCode}</Badge>;
  }
  if (statusCode >= 400 && statusCode < 500) {
    return <Badge variant="outline" className="text-amber-400 border-amber-400/30 bg-amber-400/10">{statusCode}</Badge>;
  }
  if (statusCode >= 500) {
    return <Badge variant="outline" className="text-red-400 border-red-400/30 bg-red-400/10">{statusCode}</Badge>;
  }
  return <Badge variant="outline">{statusCode}</Badge>;
}

const PAGE_SIZE = 25;

export default function RequestLogsPage() {
  const [offset, setOffset] = useState(0);
  const [username, setUsername] = useState('');
  const [modelFilter, setModelFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [sourceFilter, setSourceFilter] = useState<string>('all');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');

  const { data, isLoading } = useQuery({
    queryKey: ['request-logs', offset, username, modelFilter, statusFilter, sourceFilter, dateFrom, dateTo],
    queryFn: () => dashboardApi.getRequestLog({
      limit: PAGE_SIZE,
      offset,
      username: username || undefined,
      model_name: modelFilter || undefined,
      status_category: statusFilter === 'all' ? undefined : statusFilter === 'success' ? 'success' : statusFilter === 'error' ? 'error' : statusFilter === '4xx' ? '4xx' : undefined,
      status_code: (statusFilter === 'all' || statusFilter === 'success' || statusFilter === 'error' || statusFilter === '4xx' || isNaN(Number(statusFilter))) ? undefined : Number(statusFilter),
      source: sourceFilter === 'all' ? undefined : sourceFilter,
      start_date: dateFrom || undefined,
      end_date: dateTo || undefined,
    }),
  });

  const handlePrevPage = () => setOffset(Math.max(0, offset - PAGE_SIZE));
  const handleNextPage = () => {
    if (data && offset + PAGE_SIZE < data.total) {
      setOffset(offset + PAGE_SIZE);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <FileText className="h-5 w-5 text-muted-foreground" />
        <h2 className="text-xl font-bold">Request Logs</h2>
        {data && (
          <span className="text-sm text-muted-foreground">
            {data.total} total requests
          </span>
        )}
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-medium">Filters</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
            <Input
              placeholder="Username"
              value={username}
              onChange={(e) => { setUsername(e.target.value); setOffset(0); }}
              className="text-sm"
            />
            <Input
              placeholder="Model name"
              value={modelFilter}
              onChange={(e) => { setModelFilter(e.target.value); setOffset(0); }}
              className="text-sm"
            />
            <select
              value={statusFilter}
              onChange={(e) => { setStatusFilter(e.target.value); setOffset(0); }}
              className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            >
              <option value="all">All Status</option>
              <option value="success">Success (2xx)</option>
              <option value="error">Error (5xx)</option>
              <option value="4xx">4xx (Client Error)</option>
              <option value="400">400</option>
              <option value="401">401</option>
              <option value="403">403</option>
              <option value="404">404</option>
              <option value="429">429</option>
              <option value="500">500</option>
              <option value="503">503</option>
            </select>
            <select
              value={sourceFilter}
              onChange={(e) => { setSourceFilter(e.target.value); setOffset(0); }}
              className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            >
              <option value="all">All Sources</option>
              <option value="Ollama Native">Ollama Native</option>
              <option value="OpenAI-Compatible">OpenAI-Compatible</option>
              <option value="OpenClaw">OpenClaw</option>
              <option value="Grafana">Grafana</option>
            </select>
            <Input
              type="date"
              placeholder="From"
              value={dateFrom}
              onChange={(e) => { setDateFrom(e.target.value); setOffset(0); }}
              className="text-sm"
            />
            <Input
              type="date"
              placeholder="To"
              value={dateTo}
              onChange={(e) => { setDateTo(e.target.value); setOffset(0); }}
              className="text-sm"
            />
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setUsername('');
                setModelFilter('');
                setStatusFilter('all');
                setSourceFilter('all');
                setDateFrom('');
                setDateTo('');
                setOffset(0);
              }}
            >
              Clear
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="p-0 overflow-x-auto">
          {isLoading ? (
            <div className="p-4 space-y-3">
              {[...Array(5)].map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Time</TableHead>
                  <TableHead>User</TableHead>
                  <TableHead>Model</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Source</TableHead>
                  <TableHead>URL Path</TableHead>
                  <TableHead className="text-right">Prompt</TableHead>
                  <TableHead className="text-right">Completion</TableHead>
                  <TableHead className="text-right">Total</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Duration</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data?.logs.map((log: RequestLogItem) => (
                  <TableRow key={log.id}>
                    <TableCell className="text-xs text-muted-foreground whitespace-nowrap">
                      {log.created_at ? new Date(log.created_at).toLocaleString('en-US') : '-'}
                    </TableCell>
                    <TableCell className="font-mono text-xs">{log.username || '-'}</TableCell>
                    <TableCell className="font-mono text-xs">{log.model_name}</TableCell>
                    <TableCell className="text-xs">{log.request_type}</TableCell>
                    <TableCell className="text-xs">{log.source || '-'}</TableCell>
                    <TableCell className="text-xs font-mono" title={log.url_path || undefined}>{log.url_path || '-'}</TableCell>
                    <TableCell className="text-right text-xs">{formatNumber(log.prompt_tokens ?? 0)}</TableCell>
                    <TableCell className="text-right text-xs">{formatNumber(log.completion_tokens ?? 0)}</TableCell>
                    <TableCell className="text-right text-xs font-medium">{formatNumber(log.total_tokens ?? 0)}</TableCell>
                    <TableCell><StatusBadge statusCode={log.status_code} /></TableCell>
                    <TableCell className="text-right text-xs">{formatDuration(log.duration_ms)}</TableCell>
                  </TableRow>
                ))}
                {(!data || data.logs.length === 0) && (
                  <TableRow>
                    <TableCell colSpan={11} className="text-center text-muted-foreground py-12">
                      No request logs found
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {data && data.total > PAGE_SIZE && (
        <div className="flex items-center justify-between">
          <span className="text-sm text-muted-foreground">
            Showing {offset + 1}-{Math.min(offset + PAGE_SIZE, data.total)} of {data.total}
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline" size="sm"
              onClick={handlePrevPage}
              disabled={offset === 0}
            >
              <ChevronLeft className="h-4 w-4 mr-1" /> Previous
            </Button>
            <Button
              variant="outline" size="sm"
              onClick={handleNextPage}
              disabled={offset + PAGE_SIZE >= data.total}
            >
              Next <ChevronRight className="h-4 w-4 ml-1" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
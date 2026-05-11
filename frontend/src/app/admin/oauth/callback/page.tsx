'use client';

import { useEffect } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';

export default function GoogleOAuthCallbackPage() {
  const searchParams = useSearchParams();
  const router = useRouter();

  useEffect(() => {
    const code = searchParams.get('code');
    const state = searchParams.get('state');

    if (!code || !state) {
      router.push('/nodes?oauth=error&msg=Missing+code+or+state');
      return;
    }

    // Parse node_id from state (format: "node_id:random_token")
    const nodeIdStr = state.split(':')[0];
    const nodeId = Number(nodeIdStr);

    if (!nodeId) {
      router.push('/nodes?oauth=error&msg=Invalid+state');
      return;
    }

    const token = sessionStorage.getItem('admin_token') || '';

    // Send code to backend via POST
    fetch(`/api/proxy/admin/nodes/${nodeId}/google-auth-callback`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ code, state }),
    })
      .then(async (res) => {
        if (res.ok) {
          router.push(`/nodes/${nodeId}?oauth=success`);
        } else {
          const text = await res.text();
          router.push(`/nodes/${nodeId}?oauth=error&msg=${encodeURIComponent(text || 'OAuth failed')}`);
        }
      })
      .catch((err) => {
        router.push(`/nodes/${nodeId}?oauth=error&msg=${encodeURIComponent(err.message || 'Network error')}`);
      });
  }, [searchParams, router]);

  return (
    <div className="flex items-center justify-center min-h-screen bg-background">
      <div className="text-center space-y-4">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary mx-auto" />
        <p className="text-muted-foreground">Completing Google OAuth...</p>
      </div>
    </div>
  );
}

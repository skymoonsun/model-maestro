'use client';

import { usePathname } from 'next/navigation';
import { Sidebar } from '@/components/sidebar';
import { Header } from '@/components/header';
import React, { useState } from 'react';

const MODEL_OPTIONS = [
  { label: 'Claude Opus 4.6', value: 'claude-opus-4.6' },
  { label: 'Kimi K2.5', value: 'kimi-k2.5' },
  { label: 'Qwen3.5', value: 'qwen3.5' },
];

function ModelSelect({ model, setModel }: { model: string; setModel: (v: string) => void }) {
  return (
    <div className="flex items-center gap-2 mb-4">
      <select
        value={model}
        onChange={e => setModel(e.target.value)}
        className="border px-2 py-1 rounded text-sm"
      >
        {MODEL_OPTIONS.map(opt => (
          <option key={opt.value} value={opt.value}>{opt.label}</option>
        ))}
      </select>
      <span className="text-xs text-gray-500">Aktif model: <b>{model}</b></span>
    </div>
  );
}

export function DashboardShell({ children }: { children: React.ReactNode }) {
    const pathname = usePathname();
    const [model, setModel] = useState(MODEL_OPTIONS[0].value);

    if (pathname === '/login') {
        return <>{children}</>;
    }

    // Model bilgisi API çağrılarında kullanılacak şekilde context veya props üzerinden children'a iletilmeli.
    // Burada örneğin React.cloneElement ile child'a prop eklenebilir, veya context kullanılabilir.
    // Şimdilik children'ın bir function veya element olması varsayımıyla aşağıdaki örnek eklenmiştir:

    let withModelChild;
    if (typeof children === 'function') {
        withModelChild = children({ model });
    } else {
        // Eğer children bir component ise props ile model bilgisi verilebilir
        withModelChild = React.Children.map(children, child => {
            if (React.isValidElement(child)) {
                return React.cloneElement(child, { model });
            }
            return child;
        });
    }

    return (
        <div className="flex min-h-screen">
            <Sidebar />
            <main className="flex-1 md:pl-64 min-w-0 overflow-hidden">
                <Header model={model} />
                <div className="p-6">
                  <ModelSelect model={model} setModel={setModel} />
                  {withModelChild}
                </div>
            </main>
        </div>
    );
}

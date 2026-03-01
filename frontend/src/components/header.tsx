'use client';

import { usePathname } from 'next/navigation';

const pageTitles: Record<string, string> = {
    '/': 'Dashboard',
    '/users': 'Users',
    '/models/mappings': 'Model Mappings',
    '/models/ollama': 'Ollama Models',
    '/models/config': 'Model Config',
    '/tool-sets': 'Tool Sets',
    '/settings': 'Settings',
    '/audit-logs': 'Audit Logs',
};

export function Header() {
    const pathname = usePathname();

    const getTitle = () => {
        // Check exact match first
        if (pageTitles[pathname]) return pageTitles[pathname];
        // Check user detail page
        if (pathname.startsWith('/users/')) return 'User Detail';
        return 'Admin Panel';
    };

    return (
        <header className="h-16 border-b border-border bg-card/80 backdrop-blur-sm flex items-center px-6 sticky top-0 z-40">
            <h2 className="text-lg font-semibold">{getTitle()}</h2>
        </header>
    );
}

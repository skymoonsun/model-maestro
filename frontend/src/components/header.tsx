'use client';

import { usePathname } from 'next/navigation';
import { LogOut } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { toast } from 'sonner';
import { clearAdminToken } from '@/lib/api';

const pageTitles: Record<string, string> = {
    '/': 'Dashboard',
    '/users': 'Users',
    '/models/mappings': 'Model Mappings',
    '/models/ollama': 'Ollama Models',
    '/models/config': 'Model Config',
    '/nodes': 'Nodes',
    '/tool-sets': 'Tool Sets',
    '/settings': 'Settings',
    '/audit-logs': 'Audit Logs',
};

export function Header() {
    const pathname = usePathname();

    const getTitle = () => {
        if (pageTitles[pathname]) return pageTitles[pathname];
        if (pathname.startsWith('/users/')) return 'User Detail';
        if (pathname.startsWith('/nodes/')) return 'Node Detail';
        return 'Admin Panel';
    };

    const handleLogout = async () => {
        try {
            clearAdminToken();
            await fetch('/api/auth/logout', { method: 'POST' });
            toast.success('Logged out');
            window.location.href = '/login';
        } catch {
            toast.error('Logout failed');
        }
    };

    return (
        <header className="h-16 border-b border-border bg-card/80 backdrop-blur-sm flex items-center justify-between px-6 sticky top-0 z-40">
            <h2 className="text-lg font-semibold">{getTitle()}</h2>
            <Button
                variant="ghost"
                size="sm"
                onClick={handleLogout}
                className="text-muted-foreground hover:text-foreground"
            >
                <LogOut className="h-4 w-4 mr-2" />
                Logout
            </Button>
        </header>
    );
}

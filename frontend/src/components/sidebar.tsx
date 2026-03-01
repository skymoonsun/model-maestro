'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useState } from 'react';
import {
    LayoutDashboard,
    Users,
    Bot,
    Wrench,
    Settings,
    ClipboardList,
    ChevronDown,
    ChevronRight,
    Layers,
    Server,
    SlidersHorizontal,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { ScrollArea } from '@/components/ui/scroll-area';

interface NavItem {
    label: string;
    href: string;
    icon: React.ComponentType<{ className?: string }>;
    children?: { label: string; href: string; icon: React.ComponentType<{ className?: string }> }[];
}

const navigation: NavItem[] = [
    { label: 'Dashboard', href: '/', icon: LayoutDashboard },
    { label: 'Users', href: '/users', icon: Users },
    {
        label: 'Models',
        href: '/models',
        icon: Bot,
        children: [
            { label: 'Mappings', href: '/models/mappings', icon: Layers },
            { label: 'Ollama Models', href: '/models/ollama', icon: Server },
            { label: 'Model Config', href: '/models/config', icon: SlidersHorizontal },
        ],
    },
    { label: 'Tool Sets', href: '/tool-sets', icon: Wrench },
    { label: 'Settings', href: '/settings', icon: Settings },
    { label: 'Audit Logs', href: '/audit-logs', icon: ClipboardList },
];

export function Sidebar() {
    const pathname = usePathname();
    const [modelsOpen, setModelsOpen] = useState(pathname.startsWith('/models'));

    return (
        <aside className="hidden md:flex md:w-64 md:flex-col md:fixed md:inset-y-0 z-50">
            <div className="flex flex-col flex-grow bg-card border-r border-border">
                {/* Logo */}
                <div className="flex items-center h-16 px-6 border-b border-border">
                    <div className="flex items-center gap-3">
                        <div className="h-8 w-8 rounded-lg bg-gradient-to-br from-blue-500 to-violet-600 flex items-center justify-center">
                            <Bot className="h-4 w-4 text-white" />
                        </div>
                        <div>
                            <h1 className="text-sm font-semibold tracking-tight">Ollama Proxy</h1>
                            <p className="text-[10px] text-muted-foreground">Admin Panel</p>
                        </div>
                    </div>
                </div>

                {/* Navigation */}
                <ScrollArea className="flex-1 py-4">
                    <nav className="px-3 space-y-1">
                        {navigation.map((item) => {
                            if (item.children) {
                                const isParentActive = pathname.startsWith(item.href);
                                return (
                                    <div key={item.href}>
                                        <button
                                            onClick={() => setModelsOpen(!modelsOpen)}
                                            className={cn(
                                                'flex items-center w-full gap-3 px-3 py-2.5 text-sm rounded-lg transition-colors',
                                                isParentActive
                                                    ? 'text-foreground'
                                                    : 'text-muted-foreground hover:text-foreground hover:bg-accent'
                                            )}
                                        >
                                            <item.icon className="h-4 w-4" />
                                            <span className="flex-1 text-left">{item.label}</span>
                                            {modelsOpen ? (
                                                <ChevronDown className="h-4 w-4" />
                                            ) : (
                                                <ChevronRight className="h-4 w-4" />
                                            )}
                                        </button>
                                        {modelsOpen && (
                                            <div className="ml-4 mt-1 space-y-1">
                                                {item.children.map((child) => (
                                                    <Link
                                                        key={child.href}
                                                        href={child.href}
                                                        className={cn(
                                                            'flex items-center gap-3 px-3 py-2 text-sm rounded-lg transition-colors',
                                                            pathname === child.href
                                                                ? 'bg-accent text-foreground font-medium'
                                                                : 'text-muted-foreground hover:text-foreground hover:bg-accent'
                                                        )}
                                                    >
                                                        <child.icon className="h-4 w-4" />
                                                        <span>{child.label}</span>
                                                    </Link>
                                                ))}
                                            </div>
                                        )}
                                    </div>
                                );
                            }

                            const isActive =
                                item.href === '/' ? pathname === '/' : pathname.startsWith(item.href);

                            return (
                                <Link
                                    key={item.href}
                                    href={item.href}
                                    className={cn(
                                        'flex items-center gap-3 px-3 py-2.5 text-sm rounded-lg transition-colors',
                                        isActive
                                            ? 'bg-accent text-foreground font-medium'
                                            : 'text-muted-foreground hover:text-foreground hover:bg-accent'
                                    )}
                                >
                                    <item.icon className="h-4 w-4" />
                                    <span>{item.label}</span>
                                </Link>
                            );
                        })}
                    </nav>
                </ScrollArea>

                {/* Footer */}
                <div className="p-4 border-t border-border">
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                        <div className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
                        <span>Connected</span>
                    </div>
                </div>
            </div>
        </aside>
    );
}

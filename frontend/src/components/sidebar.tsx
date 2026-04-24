'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useState, useEffect } from 'react';
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
    Network,
    Group,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { ScrollArea } from '@/components/ui/scroll-area';

interface NavChild {
    label: string;
    href: string;
    icon: React.ComponentType<{ className?: string }>;
}

interface NavItem {
    label: string;
    href: string;
    icon: React.ComponentType<{ className?: string }>;
    children?: NavChild[];
}

const navigation: NavItem[] = [
    { label: 'Dashboard', href: '/', icon: LayoutDashboard },
    { label: 'Users', href: '/users', icon: Users },
    {
        label: 'Ollama',
        href: '/ollama',
        icon: Server,
        children: [
            { label: 'Nodes', href: '/nodes', icon: Network },
            { label: 'Models', href: '/models/ollama', icon: Bot },
        ],
    },
    {
        label: 'Models',
        href: '/models',
        icon: Layers,
        children: [
            { label: 'Mappings', href: '/models/mappings', icon: Layers },
            { label: 'Groups', href: '/models/groups', icon: Group },
            { label: 'Config', href: '/models/config', icon: SlidersHorizontal },
        ],
    },
    { label: 'Tool Sets', href: '/tool-sets', icon: Wrench },
    { label: 'Settings', href: '/settings', icon: Settings },
    { label: 'Audit Logs', href: '/audit-logs', icon: ClipboardList },
];

export function Sidebar() {
    const pathname = usePathname();
    const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});

    useEffect(() => {
        setOpenGroups((prev) => {
            const next = { ...prev };
            navigation.forEach((item) => {
                if (item.children) {
                    const isActive = item.children.some(
                        (c) =>
                            pathname === c.href ||
                            (c.href !== '/' && pathname.startsWith(c.href))
                    );
                    if (isActive) next[item.href] = true;
                }
            });
            return next;
        });
    }, [pathname]);

    const toggleGroup = (href: string, currentlyOpen: boolean) => {
        setOpenGroups((prev) => ({ ...prev, [href]: !currentlyOpen }));
    };

    const isGroupActive = (item: NavItem) => {
        if (!item.children) return false;
        return item.children.some(
            (c) => pathname === c.href || (c.href !== '/' && pathname.startsWith(c.href))
        );
    };

    return (
        <aside className="hidden md:flex md:w-64 md:flex-col md:fixed md:inset-y-0 z-50">
            <div className="flex flex-col flex-grow bg-card border-r border-border">
                {/* Logo */}
                <div className="flex items-center h-16 px-4 border-b border-border">
                    <img src="/logo.png" alt="Model Maestro" className="w-full h-auto max-h-10 object-contain" />
                </div>

                {/* Navigation */}
                <ScrollArea className="flex-1 py-4">
                    <nav className="px-3 space-y-1">
                        {navigation.map((item) => {
                            if (item.children) {
                                const isOpen = openGroups[item.href] ?? isGroupActive(item);
                                const isParentActive = isGroupActive(item);
                                return (
                                    <div key={item.href}>
                                        <button
                                            onClick={() => toggleGroup(item.href, isOpen)}
                                            className={cn(
                                                'flex items-center w-full gap-3 px-3 py-2.5 text-sm rounded-lg transition-colors',
                                                isParentActive
                                                    ? 'text-foreground'
                                                    : 'text-muted-foreground hover:text-foreground hover:bg-accent'
                                            )}
                                        >
                                            <item.icon className="h-4 w-4 shrink-0" />
                                            <span className="flex-1 text-left">{item.label}</span>
                                            {isOpen ? (
                                                <ChevronDown className="h-4 w-4 shrink-0" />
                                            ) : (
                                                <ChevronRight className="h-4 w-4 shrink-0" />
                                            )}
                                        </button>
                                        {isOpen && (
                                            <div className="ml-4 mt-1 space-y-1">
                                                {item.children.map((child) => (
                                                    <Link
                                                        key={child.href}
                                                        href={child.href}
                                                        className={cn(
                                                            'flex items-center gap-3 px-3 py-2 text-sm rounded-lg transition-colors',
                                                            pathname === child.href ||
                                                                (child.href !== '/' &&
                                                                    pathname.startsWith(child.href))
                                                                ? 'bg-accent text-foreground font-medium'
                                                                : 'text-muted-foreground hover:text-foreground hover:bg-accent'
                                                        )}
                                                    >
                                                        <child.icon className="h-4 w-4 shrink-0" />
                                                        <span>{child.label}</span>
                                                    </Link>
                                                ))}
                                            </div>
                                        )}
                                    </div>
                                );
                            }

                            const isActive =
                                item.href === '/'
                                    ? pathname === '/'
                                    : pathname.startsWith(item.href);

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
                                    <item.icon className="h-4 w-4 shrink-0" />
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

import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

const SESSION_COOKIE = 'admin_session';

export function middleware(request: NextRequest) {
    const { pathname } = request.nextUrl;

    if (pathname === '/login') {
        // Cookie alone does not mean a valid UI session — JWT lives in sessionStorage.
        // Redirecting to / when only the cookie exists trapped users in a / ↔ /login loop.
        return NextResponse.next();
    }

    if (pathname.startsWith('/api/')) {
        return NextResponse.next();
    }

    if (!request.cookies.get(SESSION_COOKIE)?.value) {
        return NextResponse.redirect(new URL('/login', request.url));
    }

    return NextResponse.next();
}

export const config = {
    matcher: ['/((?!api|_next/static|_next/image|favicon.ico|logo.png).*)'],
};

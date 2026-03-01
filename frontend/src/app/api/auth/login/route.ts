import { NextResponse } from 'next/server';
import { cookies } from 'next/headers';

const SESSION_COOKIE = 'admin_session';
const SESSION_MAX_AGE = 60 * 60 * 24 * 7; // 7 days
const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:8000';

export async function POST(request: Request) {
    try {
        const { username, password } = await request.json();

        const res = await fetch(`${BACKEND_URL}/admin/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: String(username || ''), password: String(password || '') }),
        });

        const data = await res.json().catch(() => ({}));

        if (!res.ok) {
            return NextResponse.json(
                { success: false, error: data.detail || 'Invalid credentials' },
                { status: res.status }
            );
        }

        const token = data.token;
        if (!token) {
            return NextResponse.json(
                { success: false, error: 'Invalid response from server' },
                { status: 500 }
            );
        }

        const cookieStore = await cookies();
        cookieStore.set(SESSION_COOKIE, '1', {
            httpOnly: true,
            secure: process.env.NODE_ENV === 'production',
            sameSite: 'lax',
            maxAge: SESSION_MAX_AGE,
            path: '/',
        });

        return NextResponse.json({ success: true, token });
    } catch {
        return NextResponse.json(
            { success: false, error: 'Invalid request' },
            { status: 400 }
        );
    }
}

import type { NextConfig } from "next";

const nextConfig = {
  reactCompiler: false,
  typescript: {
    ignoreBuildErrors: true,
  },
  async rewrites() {
    // API calls from the browser go to /api/proxy/* which gets rewritten
    // to the backend. In Docker: ollama-proxy:8000, locally: localhost:8000
    const backendUrl = process.env.BACKEND_URL || 'http://localhost:8000';
    return [
      {
        source: '/api/proxy/:path*',
        destination: `${backendUrl}/:path*`,
      },
    ];
  },
};

export default nextConfig;

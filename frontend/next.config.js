/** @type {import('next').NextConfig} */
// Browser talks only to this Next server (works on any preview/host domain);
// API calls are proxied server-side to the FastAPI backend (§22 — the backend
// origin and any secrets never reach client-side JS).
//
// Local dev:        defaults to http://127.0.0.1:8000 (run uvicorn locally).
// Vercel deploy:    set API_ORIGIN to your Railway backend URL (no trailing
//                   slash), e.g. https://memecoin-radar-api.up.railway.app,
//                   then trigger a redeploy (rewrites are baked at build time).
const API_ORIGIN = process.env.API_ORIGIN || "http://127.0.0.1:8000";

const nextConfig = {
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` },
    ];
  },
};

export default nextConfig;

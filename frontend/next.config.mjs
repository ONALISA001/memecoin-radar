/** @type {import('next').NextConfig} */
const nextConfig = {
  // Browser talks only to this Next server (works on any preview host);
  // API calls are proxied server-side to the FastAPI backend (§22).
  async rewrites() {
    return [
      { source: "/api/:path*", destination: "http://127.0.0.1:8000/api/:path*" },
    ];
  },
};

export default nextConfig;

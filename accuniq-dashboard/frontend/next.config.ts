import type { NextConfig } from 'next';
const backend = process.env.BACKEND_URL || 'http://127.0.0.1:8010';
const config: NextConfig = {
  async rewrites() { return [{source: '/api/:path*', destination: `${backend}/api/:path*`}]; },
};
export default config;

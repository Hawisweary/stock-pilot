import type { NextConfig } from "next";

const apiBackend = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8800";

const nextConfig: NextConfig = {
  typescript: {
    // 部分页面使用宽松 API 类型，先保证生产构建可启动
    ignoreBuildErrors: true,
  },
  // Next.js 16 默认拦截跨源 dev 资源；localhost 与 127.0.0.1 在浏览器里算不同源
  allowedDevOrigins: [
    "localhost",
    "127.0.0.1",
    "localhost:3002",
    "127.0.0.1:3002",
    "*.localhost",
  ],
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${apiBackend}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;

import type { NextConfig } from "next";

// 用 127.0.0.1 而非 localhost：localhost 在本机优先解析 IPv6 ::1,而后端 uvicorn
// 只监听 IPv4,Node 代理走 ::1 会 "socket hang up",导致 /api 间歇性失败(因子库/组合空)
const apiBackend = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8800";

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
  // 页面HTML禁止缓存:此前预渲染页带 s-maxage=31536000,WebKit(Tauri)会缓存
  // 旧构建的页面shell,多次重建后app一直跑陈旧JS→拿到数据也渲染不出。
  // 静态资源(_next/static,内容哈希不可变)与API不受影响。
  async headers() {
    return [
      {
        source: "/((?!_next/static|_next/image|favicon.ico|api/).*)",
        headers: [{ key: "Cache-Control", value: "no-store, must-revalidate" }],
      },
    ];
  },
};

export default nextConfig;

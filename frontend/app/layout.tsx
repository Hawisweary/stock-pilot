import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { Layout } from "@/components/Layout";
import { ThemeProvider } from "@/components/ThemeProvider";
import { ToastProvider } from "@/lib/useToast";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

// 所有数字/评分/代码用 mono(等宽 + tabular-nums 对齐)
const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Stock Pilot",
  description: "A股量化投研平台 · 十维评分 · 因子实验室 · 回测 · 模拟盘",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="min-h-full">
        <ThemeProvider>
          <ToastProvider>
            <Layout>{children}</Layout>
          </ToastProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}

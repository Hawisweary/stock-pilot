import type { Metadata } from "next";
import { Geist } from "next/font/google";
import "./globals.css";
import { Layout } from "@/components/Layout";
import { ThemeProvider } from "@/components/ThemeProvider";
import { ToastProvider } from "@/lib/useToast";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "AI 基本面研究员",
  description: "自动化财务分析 + 股票基本面研究 + AI辅助投资决策系统",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" className={`${geistSans.variable} h-full antialiased`}>
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

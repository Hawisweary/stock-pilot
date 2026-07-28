"use client";

import { usePathname } from "next/navigation";
import Link from "next/link";
import { LayoutDashboard, BarChart3, Database, TrendingUp, Moon, Sun, Activity, Grid3X3, FlaskConical, Briefcase, Menu, X, Filter, CalendarDays, ShieldCheck } from "lucide-react";
import { useTheme } from "@/components/ThemeProvider";
import { GlobalSearch } from "@/components/GlobalSearch";
import { useEffect, useState } from "react";

type NavItem = {
  href: string;
  label: string;
  icon: typeof LayoutDashboard;
  beta?: boolean;
  flag?: keyof FeatureFlags;
  /** 额外的激活路径前缀(用于把多个路由归到一个侧栏入口) */
  match?: string[];
};

type FeatureFlags = {
  backtest: boolean;
  portfolio: boolean;
  factor_lab: boolean;
  factor_ic: boolean;
};

// 分组导航:研究主线 / 实验(3个Beta收成1个入口,进去用顶部tab切) / 系统(数据管理+质量看板合一)
const NAV_GROUPS: { title?: string; items: NavItem[] }[] = [
  {
    items: [
      { href: "/", label: "Dashboard", icon: LayoutDashboard },
      { href: "/market", label: "市场行情", icon: Activity },
      { href: "/stocks", label: "股票列表", icon: TrendingUp },
      { href: "/screener", label: "选股筛选", icon: Filter },
      { href: "/calendar", label: "财报日历", icon: CalendarDays },
      { href: "/heatmap", label: "V5 热力图", icon: Grid3X3 },
    ],
  },
  {
    title: "实验",
    items: [
      // 回测/模拟交易/因子实验室 → 一个入口,页面内 BetaTabs 互切
      { href: "/backtest", label: "实验模块", icon: FlaskConical, beta: true, flag: "backtest",
        match: ["/portfolio", "/factors", "/ic", "/qlib"] },
    ],
  },
  {
    title: "系统",
    items: [
      // 数据管理 + 质量看板 → 一个入口,页面内 DataTabs 互切
      { href: "/data", label: "数据中心", icon: Database, match: ["/data-quality"] },
    ],
  },
];

const DEFAULT_FLAGS: FeatureFlags = {
  backtest: process.env.NEXT_PUBLIC_AFR_ENABLE_BACKTEST !== "false",
  portfolio: process.env.NEXT_PUBLIC_AFR_ENABLE_PORTFOLIO !== "false",
  factor_lab: process.env.NEXT_PUBLIC_AFR_ENABLE_FACTOR_LAB !== "false",
  factor_ic: process.env.NEXT_PUBLIC_AFR_ENABLE_FACTOR_LAB !== "false",
};

export function Layout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { theme, toggle } = useTheme();
  const [flags, setFlags] = useState<FeatureFlags>(DEFAULT_FLAGS);
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    fetch("/api/system/features")
      .then((r) => {
        if (!r.ok) throw new Error(`features ${r.status}`);
        return r.json();
      })
      .then((d) => {
        if (d && typeof d.backtest === "boolean") {
          setFlags({
            backtest: d.backtest,
            portfolio: d.portfolio,
            factor_lab: d.factor_lab,
            factor_ic: d.factor_ic,
          });
        }
      })
      .catch(() => {
        /* 后端未就绪时保留 DEFAULT_FLAGS，避免控制台报错 */
      });
  }, []);

  const navGroups = NAV_GROUPS
    .map((g) => ({ ...g, items: g.items.filter((item) => !item.flag || flags[item.flag]) }))
    .filter((g) => g.items.length > 0);

  const hit = (base: string) => pathname === base || pathname.startsWith(base + "/");
  const isActiveItem = (item: NavItem) =>
    (item.href === "/" ? pathname === "/" : hit(item.href)) ||
    (item.match ?? []).some((m) => hit(m));

  useEffect(() => { setDrawerOpen(false); }, [pathname]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "d" && e.metaKey) { e.preventDefault(); toggle(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggle]);

  const sidebarContent = (
    <>
      <div className="flex h-14 items-center border-b px-6 justify-between">
        <div className="flex items-center">
          <BarChart3 className="h-6 w-6 text-primary mr-2" />
          <span className="font-bold text-lg">Stock Pilot</span>
        </div>
        <button
          onClick={() => setDrawerOpen(false)}
          className="md:hidden p-1 rounded hover:bg-accent"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <nav className="p-4 space-y-4">
        {navGroups.map((group, gi) => (
          <div key={gi} className="space-y-1">
            {group.title && (
              <div className="px-3 pt-1 pb-0.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/50">
                {group.title}
              </div>
            )}
            {group.items.map((item) => {
              const Icon = item.icon;
              const isActive = isActiveItem(item);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors ${
                    isActive
                      ? "bg-primary/10 text-primary font-medium"
                      : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                  }`}
                >
                  <Icon className="h-4 w-4" />
                  <span className="flex-1">{item.label}</span>
                  {item.beta && (
                    <span className="text-[9px] uppercase tracking-wide text-amber-600/90 font-semibold">
                      Beta
                    </span>
                  )}
                </Link>
              );
            })}
          </div>
        ))}
      </nav>
      <div className="absolute bottom-4 left-4 right-4">
        <button
          onClick={toggle}
          className="flex items-center gap-2 w-full rounded-lg px-3 py-2 text-sm text-muted-foreground hover:bg-accent transition-colors"
        >
          {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          {theme === "dark" ? "浅色模式" : "深色模式"}
        </button>
      </div>
      <div className="absolute bottom-14 left-4 right-4 text-[9px] text-muted-foreground">
        ⌘K 搜索 · ⌘D 深色
      </div>
    </>
  );

  return (
    <div className="min-h-screen bg-background">
      {/* 桌面侧栏 */}
      <aside className="hidden md:block fixed left-0 top-0 z-40 h-screen w-56 border-r bg-card">
        {sidebarContent}
      </aside>

      {/* 移动端 header + drawer */}
      <header className="md:hidden fixed top-0 left-0 right-0 z-40 h-14 border-b bg-card flex items-center px-4 gap-3">
        <button onClick={() => setDrawerOpen(true)} className="p-1 rounded hover:bg-accent">
          <Menu className="h-5 w-5" />
        </button>
        <BarChart3 className="h-5 w-5 text-primary" />
        <span className="font-bold text-base flex-1">Stock Pilot</span>
      </header>

      {drawerOpen && (
        <>
          <div
            className="md:hidden fixed inset-0 z-40 bg-black/40"
            onClick={() => setDrawerOpen(false)}
          />
          <aside className="md:hidden fixed left-0 top-0 z-50 h-screen w-56 border-r bg-card">
            {sidebarContent}
          </aside>
        </>
      )}

      <GlobalSearch />
      <main className="md:ml-56 pt-14 md:pt-0 p-4 md:p-8">{children}</main>
    </div>
  );
}

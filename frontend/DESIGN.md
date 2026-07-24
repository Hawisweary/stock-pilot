# Stock Pilot — 前端设计规范

买方研究工作台的视觉工程约定。加新页 / 改 UI 前先读这份,保证与已硬化的十页一致。
源自 Leonxlnx taste-skill brief:冷 Slate 底、单一强调色、mono 数字、dashboard 硬化、暗色安全。

**铁律:纯视觉层。** 不动 API / 路由 / `AFR_ENABLE_*` 开关 / 后端数据。只改 className、token、图标。

---

## 1. 颜色:全部走 token,不写死

颜色定义在 [`app/globals.css`](app/globals.css) 的 `:root` / `.dark`,经 `@theme inline` 暴露为 Tailwind 工具类。
**改一处 token 值,全站生效** —— 永远不要在页面里写 `bg-blue-500` / `#3b82f6` / `rgba(...)`。

| 用途 | token / 类 | 说明 |
|------|-----------|------|
| 背景 / 卡片 | `bg-background` `bg-card` | 深色是 deep slate,**不是纯黑 #000** |
| 边框 | `border-border` | 所有 `border` 都显式带 `border-border` |
| 主文字 / 次要 | `text-foreground` `text-muted-foreground` | |
| **强调(唯一)** | `text-primary` `bg-primary` `border-primary/20` | 电光蓝。全站唯一强调色 |
| 危险 / 删除 | `text-destructive` `bg-destructive/10` | 删除、强否决、错误 |

### 涨跌 = 功能色,红绿只用在这
A 股约定 **涨=红 / 跌=绿**。红绿是**保留的功能色**,只用于价格/损益/买卖方向,不做装饰。

```
--up   = 红(涨 / 正收益 / 买入)   → 类 text-up / bg-up,或 style={{ background: "var(--up)" }}
--down = 绿(跌 / 负收益 / 卖出)   → 类 text-down / bg-down
```

用法:`className={pnl >= 0 ? "text-up" : "text-down"}`。买按钮红、卖按钮绿。

### 评分 ≠ 涨跌:用单色顺序蓝标度
评分质量若也用红绿黄,会和涨跌撞语义。统一走 [`lib/scoreColors.ts`](lib/scoreColors.ts):
高分=强调蓝加粗、中分=foreground、低分=muted;热力背景用 `bg-primary/15 → /[0.06] → muted`。
**任何"分数着色"都调 `scoreTextClass()` / `scoreBgClass()`,不要自己写三档红绿黄。**

### 分类色(不得已才用)
报告类型这种真·多分类、且颜色是唯一信号(月历格子)时,才用分类调色板。约束:
- **禁**:紫/indigo(AI 味)、neon、纯红纯绿(留给涨跌)
- 用暗色安全写法:`bg-amber-500/15 text-amber-700 dark:text-amber-500`(opacity 底 + dark: 文字)
- 首选顺序:强调蓝 → amber → sky → 中性 muted

### 暗色安全自查
- ❌ `bg-blue-50` `bg-green-100` `text-blue-800`(粉彩底,深色下发白/看不见)
- ✅ `bg-primary/5` `bg-amber-500/10` + `dark:text-amber-500`
- 图表/canvas 用 `var(--border)` / `var(--primary)` / `var(--up)`;canvas 里 `getComputedStyle(document.documentElement).getPropertyValue("--primary")`
- 半透明热力:`color-mix(in oklab, var(--up) ${pct}%, transparent)`

---

## 2. 数字:全部 mono + 等宽对齐

价格、分数、百分比、金额、日期、股数 —— 一律 `font-mono tabular-nums`。
`globals.css` 已给 `.font-mono/code/kbd` 加了 `font-variant-numeric: tabular-nums`;显式加类最稳。

```jsx
<span className="font-mono tabular-nums">{price.toFixed(2)}</span>
```

---

## 3. 密度与形状:硬化,不装饰

- **页头**:每页顶部 `border-b border-border pb-3` + `<h1 className="text-xl font-semibold tracking-tight">`。副标题 `text-sm text-muted-foreground`。**不用** `text-2xl font-bold`。
- **圆角收紧**:`--radius: 0.375rem`。用 `rounded-md`;**不用** `rounded-xl` / `rounded-2xl`。药丸/chip 也用 `rounded-md`,不用 `rounded-full`。
- **Card**([`components/ui/card.tsx`](components/ui/card.tsx)):已改为 `rounded-md border border-border shadow-none`,间距收紧。直接用,别加回阴影。
- **分区优先 border / divide**:多用 `border-b` `divide-x divide-border` `divide-y`,少用彩色卡片堆叠。
- 状态条这类并列指标 → `grid divide-x divide-border` + `StatTile`。

---

## 4. 图标:lucide,零 emoji

**禁一切 emoji**(🚀📊⚠️✅🔔🔒⚡ …),包括后端返回的标签(用展示层 `stripEmoji()` 剥离,不改 API)。
用 [`lucide-react`](https://lucide.dev):告警=`AlertTriangle`、成功=`CheckCircle2`、提醒=`Bell`、锁=`Lock`、刷新=`RotateCw`、筛选=`Filter`…
标题图标一般 `text-muted-foreground`,尺寸 `h-3.5 w-3.5`(内联)/ `h-5 w-5`(页头)。

---

## 5. 状态:骨架 / 空 / 错,可行动

复用 [`components/ui/data-ui.tsx`](components/ui/data-ui.tsx):`Skeleton` / `EmptyState` / `ErrorState({onRetry})` / `StatTile`。
- Loading:与真实布局**同构**的骨架(不是一个转圈)。
- Error:`<ErrorState message onRetry={reload} />`,永远给重试。
- 成功/警告状态徽章:成功=muted(安静)、失败=destructive、警告=amber、就绪=primary。**不要**用 emerald(撞跌绿)。

---

## 6. 共享件(改这些 = 多页同时生效)

| 文件 | 影响 |
|------|------|
| [`app/globals.css`](app/globals.css) | 全站 token |
| [`lib/scoreColors.ts`](lib/scoreColors.ts) | 所有评分着色 |
| [`components/ui/card.tsx`](components/ui/card.tsx) | 所有卡片 |
| [`components/ui/data-ui.tsx`](components/ui/data-ui.tsx) | 骨架/空/错/StatTile |
| [`components/BetaShell.tsx`](components/BetaShell.tsx) | 回测/模拟交易/因子实验室 三页外壳 |
| [`components/MarketOpsButtons.tsx`](components/MarketOpsButtons.tsx) | 数据管理工具栏 |
| [`components/V5ScorePanel.tsx`](components/V5ScorePanel.tsx) | 个股/相关页的 V5 评分 |

**优先改共享件**;能在 token / lib / 共享组件解决的,不要在单页硬编码。

---

## 7. 新页 checklist

- [ ] 页头 `border-b` + `text-xl font-semibold tracking-tight` + 副标题
- [ ] 所有颜色走 token(grep 自查:`text-(red|green|blue|purple|indigo|gray)-[0-9]` 应为空,amber 警告除外)
- [ ] 红绿只在涨跌/损益/买卖;评分走 `scoreColors`
- [ ] 数字全 `font-mono tabular-nums`
- [ ] 零 emoji(grep 自查),图标用 lucide
- [ ] `rounded-md`、`border-border`、无 `rounded-xl`/`rounded-full`
- [ ] Loading 骨架同构、Error 可重试
- [ ] 深浅色都过一遍(preview `preview_resize` colorScheme dark/light)
- [ ] `npm run build` 通过

---

*十页硬化提交:`5248704`(设计系统)→ `075c7d8`(收官)。见 git log `UI 第N步`。*

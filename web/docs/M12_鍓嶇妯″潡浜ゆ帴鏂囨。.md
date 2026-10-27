# M12 HANDOFF：Web 桌面控制台与手机端

## 当前交付范围：T9.2a 设计令牌底座

| 项 | 路径 |
|---|---|
| 设计令牌（CSS 变量，§17.3） | `web/src/styles/tokens.css` |
| 全局基础样式（.panel/.glass/.num/聚焦环/减弱动效） | `web/src/styles/base.css` |
| 对比度工具（测试用） | `web/src/styles/color.ts` |
| 基础组件 | `web/src/ui/`：Button、Tag、StatusDot、ModeBadge、Num、SymbolLabel、Panel（四态+错误边界）、Input、Segmented、密度切换 |
| 令牌验收页（`/dev/tokens`） | `web/src/dev/TokensPage.tsx` |
| 工程配置 | `web/package.json`、`vite.config.ts`、`tsconfig.json`、`eslint.config.js`、`console.html` |

## 运行与验证

```bash
cd web
npm install
npm run dev        # 打开 /console.html，即令牌验收页
npx tsc --noEmit
npx eslint src
npx vitest run
npx vite build
```

## 约定与注意事项

- 原型 `web/index.html`、`app.js`、`styles.css` **未改动**，新入口为 `web/console.html`，避免覆盖原型。
- 业务样式禁止硬编码色值/字号/`transition: all`/渐变，由 `hardcode.test.ts` 自动检查（F1）。
- `ModeBadge` 遇到未知模式直接抛错，不会默认显示为模拟盘（F3）。
- `formatNumber` 仅做展示格式化，不做业务计算；资金类数据按契约保持 Decimal 字符串传入。
- 只消费 API，未自行定义任何业务字段；未使用 `src/dquant/contracts/` 以外的模型。

## 与计划书的偏差（待总控确认）

1. **未引入 Tailwind v4、Radix、Zustand、TanStack、cmdk、Motion**：T9.2a 只需令牌与基础组件，使用纯 CSS 变量；这些依赖将在 T9.2b 及后续按需引入。
2. **字体未自托管**：`--font-sans`/`--font-mono` 已按规范声明，但 woff2 文件尚未放入 `web/public/fonts/`，当前回退到系统字体。需要提供字体文件（Inter Variable、JetBrains Mono，注意许可）。
3. **图标库 lucide-react 已安装但尚未使用**。
4. 令牌页以 `console.html` 直接渲染，路由 `/dev/tokens` 将在 T9.2b 路由落地后接入。
5. 额外新增令牌（规范未列出但组件需要）：`--warn-line`、`--neutral-muted/line`、`--text-on-solid`、`--solid-white(-hover)`、`--focus-halo`、`--tag-h` 等，均为规范数值的派生，无新增色相。

## 公共接口变更申请

无。

## 未完成 / 后续

- T9.2a 的“用户审美验收”需人工打开令牌页确认。
- T9.2b～e 尚未开始；业务页面需等 M11 API 契约与 `contracts/fixtures/`。
- 1280/1440/1920/390 四宽度截图验收（F5）留待页面落地后用 `tests/e2e/` 补充。

---

## T9.2b 应用框架（已完成）

| 项 | 路径 |
|---|---|
| 路由表、主导航 7 项、手机标签栏 4 项、G 跳转键 | `web/src/app/routes.ts` |
| 应用框架（顶栏/内容区/状态栏/手机标签栏） | `web/src/app/AppShell.tsx`、`TopBar.tsx`、`StatusBar.tsx`、`MobileTabBar.tsx` |
| 路由装配、建设中占位页、404 | `web/src/app/AppRoutes.tsx` |
| 命令面板（cmdk，玻璃浮层） | `web/src/app/CommandPalette.tsx` |
| 全局快捷键 Ctrl/⌘+K、G→O/S/W/P/R、D、Esc | `web/src/app/useHotkeys.ts` |
| 全局状态（模式、密度、连接、风控、待确认数、面板开关） | `web/src/app/store.ts`（zustand） |
| 北京时间格式化（UTC 毫秒 → Asia/Shanghai） | `web/src/app/time.ts` |
| 样式（断点 <768 手机布局，含安全区） | `web/src/app/app.css` |

### 行为约定

- 使用 **HashRouter**：访问地址形如 `console.html#/risk`。原因：`web/index.html` 原型仍保留，History 回退会命中原型页。M11 邮件确认链接需按 hash 形式生成，或后续由总控决定改为 History 并调整部署。
- 模式徽标与顶栏底线：模拟盘琥珀、实盘红；非法模式直接抛错。
- 业务页暂为“页面建设中”占位，不展示任何伪造数据。
- 单键快捷键在输入框内、带修饰键、命令面板打开时均不触发；G 序列 1 秒内有效。
- 状态栏的“风控”异常项可点击跳转 `/risk`。
- store 中的 `mode / connection / risk / pendingConfirmCount` 目前无数据源，等 M11 REST/WebSocket 接入后由 API 层写入，前端不复制风控规则。

### 新增依赖

`react-router-dom@6`、`zustand`、`cmdk`。

### 与计划书的偏差

1. **无侧栏**：T9.2b 任务描述含“侧栏”，但 §17.4 桌面布局只有顶栏 + 状态栏；工作台左栏属于 T9.2c。按 §17.4 实现。
2. **命令面板暂只含页面跳转与“切换密度”**：“立即扫描 / 停止开仓”等动作需要 M11 API，不在无后端时伪造。
3. **`?` 快捷键帮助、`J/K/Enter/1/2/3/F` 等页面级快捷键**留待对应页面（T9.2c）。
4. **登录页 `/login`** 随 M11 鉴权落地。
5. 手机“信号”标签暂指向 `/`，T9.2e 再提供独立信号页。
6. `tokens.css` 新增 `--topbar-h-mobile`(44px)、`--tabbar-h`(56px)、`--touch-min`(44px)，均来自 §17.7。

### 验证（2026-10-25）

`tsc --noEmit`、`eslint src`、`vitest run`（9 个测试文件全部通过）、`vite build` 均通过。

### 未验证项

- F5：1280/1440/1920/390 截图验收尚未做（需浏览器 e2e，`tests/e2e/` 待建）。
- F8：键盘聚焦环目前仅靠全局 `:focus-visible` 样式，未做自动化检查。

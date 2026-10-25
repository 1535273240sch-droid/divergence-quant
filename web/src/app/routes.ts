export interface NavItem {
  label: string;
  path: string;
}

export interface PageDef {
  path: string;
  title: string;
}

/** 全部页面（§17.6）；/login 属于壳外页面，随 M11 鉴权一起落地 */
export const PAGES: readonly PageDef[] = [
  { path: "/", title: "总览" },
  { path: "/screener", title: "选币器" },
  { path: "/workspace", title: "工作台" },
  { path: "/positions", title: "持仓" },
  { path: "/confirm", title: "待确认信号" },
  { path: "/strategies", title: "策略与 AI 进化" },
  { path: "/backtest", title: "回测" },
  { path: "/risk", title: "风控" },
  { path: "/logs", title: "系统日志" },
  { path: "/settings", title: "设置" },
  { path: "/dev/tokens", title: "设计令牌" },
];

export const ROUTE_PATHS: readonly string[] = PAGES.map((p) => p.path);

/** 顶栏主导航 7 项（§17.4） */
export const NAV_ITEMS: readonly NavItem[] = [
  { label: "总览", path: "/" },
  { label: "选币器", path: "/screener" },
  { label: "工作台", path: "/workspace" },
  { label: "持仓", path: "/positions" },
  { label: "策略", path: "/strategies" },
  { label: "回测", path: "/backtest" },
  { label: "风控", path: "/risk" },
];

/** 手机端底部标签栏（§17.7）；信号页在 T9.2e 实现前先落到总览 */
export const MOBILE_TABS: readonly NavItem[] = [
  { label: "信号", path: "/" },
  { label: "待确认", path: "/confirm" },
  { label: "持仓", path: "/positions" },
  { label: "我的", path: "/settings" },
];

/** G → O / S / W / P / R 跳转（§17.12） */
export const GOTO_KEYS: Readonly<Record<string, string>> = {
  o: "/",
  s: "/screener",
  w: "/workspace",
  p: "/positions",
  r: "/risk",
};

export function resolveGotoKey(key: string): string | null {
  const k = key.toLowerCase();
  return Object.hasOwn(GOTO_KEYS, k) ? GOTO_KEYS[k] : null;
}

import { describe, expect, it } from "vitest";
import { GOTO_KEYS, MOBILE_TABS, NAV_ITEMS, ROUTE_PATHS, resolveGotoKey } from "./routes";

describe("路由表（§17.4 / §17.7）", () => {
  it("主导航恰好 7 项且顺序固定", () => {
    expect(NAV_ITEMS.map((i) => i.label)).toEqual(["总览", "选币器", "工作台", "持仓", "策略", "回测", "风控"]);
  });

  it("导航路径唯一", () => {
    const paths = NAV_ITEMS.map((i) => i.path);
    expect(new Set(paths).size).toBe(paths.length);
  });

  it("手机端底部标签栏 4 项：信号 / 待确认 / 持仓 / 我的", () => {
    expect(MOBILE_TABS.map((t) => t.label)).toEqual(["信号", "待确认", "持仓", "我的"]);
  });

  it("所有导航与标签目标都在路由表内", () => {
    for (const item of [...NAV_ITEMS, ...MOBILE_TABS]) {
      expect(ROUTE_PATHS).toContain(item.path);
    }
  });

  it("包含计划书 §17.6 全部页面路径", () => {
    for (const p of ["/", "/screener", "/workspace", "/positions", "/confirm", "/strategies", "/backtest", "/risk", "/logs", "/settings", "/dev/tokens"]) {
      expect(ROUTE_PATHS).toContain(p);
    }
  });
});

describe("G 跳转快捷键（§17.12）", () => {
  it.each([
    ["o", "/"],
    ["s", "/screener"],
    ["w", "/workspace"],
    ["p", "/positions"],
    ["r", "/risk"],
  ])("G → %s 跳转 %s", (key, path) => {
    expect(resolveGotoKey(key)).toBe(path);
  });

  it("大小写不敏感", () => {
    expect(resolveGotoKey("P")).toBe("/positions");
  });

  it("未知按键返回 null", () => {
    expect(resolveGotoKey("x")).toBeNull();
    expect(resolveGotoKey("")).toBeNull();
    expect(resolveGotoKey("Enter")).toBeNull();
  });

  it("只定义规范中的 5 个键", () => {
    expect(Object.keys(GOTO_KEYS).sort()).toEqual(["o", "p", "r", "s", "w"]);
  });
});

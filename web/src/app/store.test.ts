import { beforeEach, describe, expect, it } from "vitest";
import { initialAppState, useAppStore } from "./store";

describe("全局状态 store", () => {
  beforeEach(() => {
    localStorage.clear();
    useAppStore.setState(initialAppState());
  });

  it("默认模拟盘、紧凑、未连接、风控正常", () => {
    const s = useAppStore.getState();
    expect(s.mode).toBe("paper");
    expect(s.density).toBe("compact");
    expect(s.connection).toBe("idle");
    expect(s.risk).toBe("normal");
    expect(s.pendingConfirmCount).toBe(0);
    expect(s.paletteOpen).toBe(false);
  });

  it("setMode 合法值生效，非法值抛错且不改变状态", () => {
    useAppStore.getState().setMode("live");
    expect(useAppStore.getState().mode).toBe("live");
    // @ts-expect-error 故意传入非法值
    expect(() => useAppStore.getState().setMode("demo")).toThrow(/未知交易模式/);
    expect(useAppStore.getState().mode).toBe("live");
  });

  it("setConnection 记录状态与延迟", () => {
    useAppStore.getState().setConnection("connected", 32);
    expect(useAppStore.getState().connection).toBe("connected");
    expect(useAppStore.getState().latencyMs).toBe(32);
  });

  it("断开连接时延迟清空", () => {
    useAppStore.getState().setConnection("connected", 32);
    useAppStore.getState().setConnection("error");
    expect(useAppStore.getState().latencyMs).toBeNull();
  });

  it.each([-1, 1.5, Number.NaN])("延迟 %s 非法时抛错", (ms) => {
    expect(() => useAppStore.getState().setConnection("connected", ms)).toThrow(/延迟/);
  });

  it("setRisk 校验", () => {
    useAppStore.getState().setRisk("halted");
    expect(useAppStore.getState().risk).toBe("halted");
    // @ts-expect-error 故意传入非法值
    expect(() => useAppStore.getState().setRisk("ok")).toThrow(/未知风控状态/);
  });

  it("待确认数量必须是非负整数", () => {
    useAppStore.getState().setPendingConfirmCount(3);
    expect(useAppStore.getState().pendingConfirmCount).toBe(3);
    expect(() => useAppStore.getState().setPendingConfirmCount(-1)).toThrow(/待确认/);
    expect(() => useAppStore.getState().setPendingConfirmCount(1.2)).toThrow(/待确认/);
  });

  it("密度切换同步写入根节点（幂等）", () => {
    useAppStore.getState().setDensity("comfortable");
    useAppStore.getState().setDensity("comfortable");
    expect(document.documentElement.dataset.density).toBe("comfortable");
    useAppStore.getState().toggleDensity();
    expect(useAppStore.getState().density).toBe("compact");
    expect(document.documentElement.dataset.density).toBe("compact");
  });

  it("命令面板开关", () => {
    useAppStore.getState().togglePalette();
    expect(useAppStore.getState().paletteOpen).toBe(true);
    useAppStore.getState().closePalette();
    expect(useAppStore.getState().paletteOpen).toBe(false);
    useAppStore.getState().openPalette();
    useAppStore.getState().openPalette();
    expect(useAppStore.getState().paletteOpen).toBe(true);
  });
});

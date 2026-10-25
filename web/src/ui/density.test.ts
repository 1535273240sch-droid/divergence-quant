import { beforeEach, describe, expect, it } from "vitest";
import { DENSITY_STORAGE_KEY, applyDensity, loadDensity, toggleDensity } from "./density";

describe("密度切换", () => {
  beforeEach(() => {
    localStorage.clear();
    delete document.documentElement.dataset.density;
  });

  it("默认紧凑", () => {
    expect(loadDensity()).toBe("compact");
  });

  it("applyDensity 写入 data-density 并持久化", () => {
    applyDensity("comfortable");
    expect(document.documentElement.dataset.density).toBe("comfortable");
    expect(localStorage.getItem(DENSITY_STORAGE_KEY)).toBe("comfortable");
    expect(loadDensity()).toBe("comfortable");
  });

  it("重复应用同一密度幂等", () => {
    applyDensity("compact");
    applyDensity("compact");
    expect(document.documentElement.dataset.density).toBe("compact");
  });

  it("toggleDensity 在两档间切换并返回新值", () => {
    applyDensity("compact");
    expect(toggleDensity()).toBe("comfortable");
    expect(document.documentElement.dataset.density).toBe("comfortable");
    expect(toggleDensity()).toBe("compact");
  });

  it("非法密度抛错且不改变现状", () => {
    applyDensity("compact");
    // @ts-expect-error 故意传入非法值
    expect(() => applyDensity("huge")).toThrow(/未知密度/);
    expect(document.documentElement.dataset.density).toBe("compact");
  });

  it("存储中是脏数据时回退为默认值", () => {
    localStorage.setItem(DENSITY_STORAGE_KEY, "garbage");
    expect(loadDensity()).toBe("compact");
  });

  it("存储不可用时仍能应用，不崩溃", () => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = () => {
      throw new Error("QuotaExceeded");
    };
    try {
      expect(() => applyDensity("comfortable")).not.toThrow();
      expect(document.documentElement.dataset.density).toBe("comfortable");
    } finally {
      Storage.prototype.setItem = original;
    }
  });
});

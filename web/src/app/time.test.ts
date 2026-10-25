import { describe, expect, it } from "vitest";
import { formatBeijingTime, formatBeijingClock } from "./time";

describe("北京时间格式化（契约：存储 UTC 毫秒，展示 Asia/Shanghai）", () => {
  it("MM-DD HH:mm", () => {
    // 2026-10-24 06:05 UTC = 14:05 北京
    expect(formatBeijingTime(Date.UTC(2026, 9, 24, 6, 5))).toBe("10-24 14:05");
  });

  it("跨日换算（UTC 18:30 → 次日 02:30）", () => {
    expect(formatBeijingTime(Date.UTC(2026, 11, 31, 18, 30))).toBe("01-01 02:30");
  });

  it("时钟精确到秒", () => {
    expect(formatBeijingClock(Date.UTC(2026, 9, 24, 6, 5, 9))).toBe("10-24 14:05:09");
  });

  it.each([Number.NaN, Number.POSITIVE_INFINITY, -1.5])("非法时间戳 %s 抛错", (ts) => {
    expect(() => formatBeijingTime(ts)).toThrow(/时间戳/);
  });

  it("非整数毫秒被拒绝，防止误传秒级小数", () => {
    expect(() => formatBeijingTime(1.5)).toThrow(/时间戳/);
  });
});

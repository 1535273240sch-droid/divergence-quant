import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { contrastRatio, parseColor } from "./color";

const css = readFileSync(join(__dirname, "tokens.css"), "utf-8");

/** 提取某个选择器块内的自定义属性 */
function readBlock(selector: string): Record<string, string> {
  const start = css.indexOf(selector + " {");
  if (start < 0) throw new Error(`tokens.css 缺少选择器 ${selector}`);
  const end = css.indexOf("}", start);
  const body = css.slice(start, end);
  const result: Record<string, string> = {};
  for (const m of body.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
    result[m[1]] = m[2].trim();
  }
  return result;
}

const root = readBlock(":root");
const compact = readBlock('[data-density="compact"]');
const comfortable = readBlock('[data-density="comfortable"]');

describe("设计令牌：色彩（§17.3.1）", () => {
  it.each([
    ["--bg-canvas", "#000000"],
    ["--bg-surface-1", "#0A0B0D"],
    ["--bg-surface-2", "#111316"],
    ["--bg-surface-3", "#181B1F"],
    ["--bg-glass", "rgba(18, 22, 28, 0.65)"],
    ["--bg-overlay", "rgba(0, 0, 0, 0.6)"],
    ["--border-hairline", "rgba(255, 255, 255, 0.06)"],
    ["--border-default", "rgba(255, 255, 255, 0.10)"],
    ["--border-strong", "rgba(255, 255, 255, 0.16)"],
    ["--border-glass", "rgba(255, 255, 255, 0.12)"],
    ["--text-primary", "#FFFFFF"],
    ["--text-secondary", "#CBD5E1"],
    ["--text-tertiary", "rgba(203, 213, 225, 0.55)"],
    ["--text-disabled", "rgba(203, 213, 225, 0.32)"],
    ["--up", "#00F5A0"],
    ["--up-muted", "rgba(0, 245, 160, 0.12)"],
    ["--up-line", "rgba(0, 245, 160, 0.24)"],
    ["--down", "#FF4D5E"],
    ["--down-muted", "rgba(255, 77, 94, 0.12)"],
    ["--down-line", "rgba(255, 77, 94, 0.24)"],
    ["--neutral-regime", "#94A3B8"],
    ["--warn", "#FFB547"],
    ["--warn-muted", "rgba(255, 181, 71, 0.12)"],
    ["--warn-line", "rgba(255, 181, 71, 0.24)"],
    ["--focus-ring", "#FFFFFF"],
  ])("%s = %s", (name, value) => {
    expect(root[name]).toBe(value);
  });

  it("画布必须是纯黑", () => {
    expect(parseColor(root["--bg-canvas"])).toEqual({ r: 0, g: 0, b: 0, a: 1 });
  });

  it("令牌中不允许出现渐变", () => {
    expect(css).not.toMatch(/gradient\(/);
  });
});

describe("设计令牌：字体、间距、圆角、动效", () => {
  it("字体栈包含自托管字体与中文回退", () => {
    expect(root["--font-sans"]).toContain("Inter Variable");
    expect(root["--font-sans"]).toContain("Microsoft YaHei UI");
    expect(root["--font-mono"]).toContain("JetBrains Mono");
  });

  it("间距遵循 4px 网格 sp-1..sp-8", () => {
    const expected = [4, 8, 12, 16, 20, 24, 32, 48];
    expected.forEach((px, i) => {
      expect(root[`--sp-${i + 1}`]).toBe(`${px}px`);
    });
  });

  it("圆角 4/6/8/12", () => {
    expect(root["--r-sm"]).toBe("4px");
    expect(root["--r-md"]).toBe("6px");
    expect(root["--r-lg"]).toBe("8px");
    expect(root["--r-xl"]).toBe("12px");
  });

  it("顶栏 48px、状态栏 24px", () => {
    expect(root["--topbar-h"]).toBe("48px");
    expect(root["--statusbar-h"]).toBe("24px");
  });

  it("动效时长与缓动", () => {
    expect(root["--dur-instant"]).toBe("80ms");
    expect(root["--dur-fast"]).toBe("140ms");
    expect(root["--dur-base"]).toBe("200ms");
    expect(root["--dur-slow"]).toBe("320ms");
    expect(root["--dur-chart"]).toBe("600ms");
    expect(root["--ease-out"]).toBe("cubic-bezier(0.22, 1, 0.36, 1)");
    expect(root["--ease-in-out"]).toBe("cubic-bezier(0.65, 0, 0.35, 1)");
  });
});

describe("设计令牌：密度切换（§17.3.2/17.3.3）", () => {
  const densityKeys = [
    "--fs-display",
    "--fs-h1",
    "--fs-h2",
    "--fs-body",
    "--fs-small",
    "--fs-label",
    "--fs-price-lg",
    "--row-h",
    "--gap-panel",
    "--pad-panel",
    "--ctl-h-sm",
    "--ctl-h-md",
    "--ctl-h-lg",
  ];

  it("紧凑与舒适模式定义同一组变量，避免切换后缺值", () => {
    for (const key of densityKeys) {
      expect(compact[key], `compact 缺少 ${key}`).toBeDefined();
      expect(comfortable[key], `comfortable 缺少 ${key}`).toBeDefined();
    }
  });

  it("紧凑模式数值", () => {
    expect(compact["--fs-display"]).toBe("28px");
    expect(compact["--fs-body"]).toBe("12px");
    expect(compact["--row-h"]).toBe("28px");
    expect(compact["--gap-panel"]).toBe("4px");
    expect(compact["--pad-panel"]).toBe("12px");
    expect(compact["--ctl-h-sm"]).toBe("24px");
    expect(compact["--ctl-h-md"]).toBe("28px");
    expect(compact["--ctl-h-lg"]).toBe("36px");
  });

  it("舒适模式数值（控件高度各 +4）", () => {
    expect(comfortable["--fs-display"]).toBe("32px");
    expect(comfortable["--fs-body"]).toBe("13px");
    expect(comfortable["--row-h"]).toBe("36px");
    expect(comfortable["--gap-panel"]).toBe("8px");
    expect(comfortable["--pad-panel"]).toBe("16px");
    expect(comfortable["--ctl-h-sm"]).toBe("28px");
    expect(comfortable["--ctl-h-md"]).toBe("32px");
    expect(comfortable["--ctl-h-lg"]).toBe("40px");
  });
});

describe("对比度（F9）", () => {
  const canvas = parseColor(root["--bg-canvas"]);
  const surface1 = parseColor(root["--bg-surface-1"]);

  it.each(["--text-primary", "--text-secondary", "--up", "--down", "--warn"])(
    "%s 在画布与面板上对比度 ≥ 4.5",
    (name) => {
      const fg = parseColor(root[name]);
      expect(contrastRatio(fg, canvas)).toBeGreaterThanOrEqual(4.5);
      expect(contrastRatio(fg, surface1)).toBeGreaterThanOrEqual(4.5);
    },
  );

  it("三级灰仅用于非关键辅助信息，但仍应可读（≥ 3）", () => {
    const fg = parseColor(root["--text-tertiary"]);
    expect(contrastRatio(fg, canvas)).toBeGreaterThanOrEqual(3);
  });
});

describe("color 工具函数", () => {
  it("解析 hex 与 rgba", () => {
    expect(parseColor("#00F5A0")).toEqual({ r: 0, g: 245, b: 160, a: 1 });
    expect(parseColor("rgba(255, 255, 255, 0.5)")).toEqual({ r: 255, g: 255, b: 255, a: 0.5 });
  });

  it("非法颜色抛出明确错误", () => {
    expect(() => parseColor("red")).toThrow(/无法解析颜色/);
    expect(() => parseColor("")).toThrow(/无法解析颜色/);
  });

  it("黑白对比度为 21", () => {
    const black = parseColor("#000000");
    const white = parseColor("#FFFFFF");
    expect(contrastRatio(white, black)).toBeCloseTo(21, 1);
  });
});

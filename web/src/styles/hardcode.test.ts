import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

/** 验收 F1：业务组件与样式中不得硬编码色值、字号、过渡 all（tokens.css 除外） */
const srcRoot = join(__dirname, "..");

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else out.push(full);
  }
  return out;
}

const files = walk(srcRoot).filter((f) => {
  const rel = relative(srcRoot, f).replaceAll("\\", "/");
  if (rel === "styles/tokens.css") return false;
  if (/\.test\.tsx?$/.test(rel) || rel.startsWith("test/")) return false;
  if (rel === "styles/color.ts") return false;
  return /\.(css|tsx?)$/.test(rel);
});

describe("F1 令牌化：禁止硬编码", () => {
  it("存在待检查的文件", () => {
    expect(files.length).toBeGreaterThan(0);
  });

  it.each(files.map((f) => [relative(srcRoot, f).replaceAll("\\", "/"), f]))(
    "%s 不含色值/字号/transition: all/渐变",
    (_rel, file) => {
      const text = readFileSync(file, "utf-8");
      expect(text, "十六进制色值").not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
      expect(text, "rgb/hsl 色值").not.toMatch(/\b(rgba?|hsla?)\(/);
      expect(text, "px 字号").not.toMatch(/font-size\s*:\s*[\d.]+px/);
      expect(text, "transition: all").not.toMatch(/transition\s*:\s*all/);
      expect(text, "渐变").not.toMatch(/gradient\(/);
    },
  );
});

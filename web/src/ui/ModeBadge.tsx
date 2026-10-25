import { Tag } from "./Tag";

export type TradingMode = "paper" | "live";

const MODE_META: Record<TradingMode, { label: string; tone: "warn" | "down" }> = {
  paper: { label: "模拟盘", tone: "warn" },
  live: { label: "实盘", tone: "down" },
};

/**
 * 模式徽标（安全关键）：模拟盘=琥珀，实盘=红。
 * 非法模式直接抛错，绝不默认当作模拟盘显示。
 */
export function ModeBadge({ mode }: { mode: TradingMode }) {
  const meta = Object.hasOwn(MODE_META, mode) ? MODE_META[mode] : undefined;
  if (!meta) {
    throw new Error(`未知交易模式: ${String(mode)}`);
  }
  return (
    <Tag tone={meta.tone} className="mode-badge" data-mode={mode}>
      {meta.label}
    </Tag>
  );
}

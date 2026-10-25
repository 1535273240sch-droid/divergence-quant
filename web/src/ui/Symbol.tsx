import { Tag } from "./Tag";

const SEGMENT_RE = /^[A-Z0-9]+$/;

interface ParsedInstId {
  base: string;
  quote: string;
  perpetual: boolean;
}

function parseInstId(instId: string): ParsedInstId {
  const parts = instId.split("-");
  const valid = parts.every((p) => SEGMENT_RE.test(p));
  if (valid && parts.length === 2) {
    return { base: parts[0], quote: parts[1], perpetual: false };
  }
  if (valid && parts.length === 3 && parts[2] === "SWAP") {
    return { base: parts[0], quote: parts[1], perpetual: true };
  }
  throw new Error(`instId 格式非法: "${instId}"（期望 BASE-QUOTE 或 BASE-QUOTE-SWAP）`);
}

/** 币种名称：BTC 白色粗体 + -USDT 次级灰；永续附小标签 */
export function SymbolLabel({ instId }: { instId: string }) {
  const { base, quote, perpetual } = parseInstId(instId);
  return (
    <span className="symbol">
      <span className="symbol-base">{base}</span>
      <span className="symbol-quote">{`-${quote}`}</span>
      {perpetual ? <Tag>永续</Tag> : null}
    </span>
  );
}

import type { HTMLAttributes } from "react";
import { type FormatOptions, type NumericInput, formatNumber, parseNumeric } from "./format";

export interface NumProps extends FormatOptions, Omit<HTMLAttributes<HTMLSpanElement>, "children"> {
  value: NumericInput;
  /** 按正负着色：涨绿、跌红、零/空为中性 */
  colorize?: boolean;
}

type Tone = "up" | "down" | "flat";

function toneOf(value: NumericInput): Tone {
  const n = parseNumeric(value);
  if (n === null || n === 0) return "flat";
  return n > 0 ? "up" : "down";
}

/** 等宽数字：所有价格、百分比、数量统一使用 */
export function Num({ value, digits, signed, unit, group, colorize = false, className, ...rest }: NumProps) {
  const text = formatNumber(value, { digits, signed, unit, group });
  const classes = ["num", className].filter(Boolean).join(" ");
  return (
    <span {...rest} className={classes} data-tone={colorize ? toneOf(value) : undefined}>
      {text}
    </span>
  );
}

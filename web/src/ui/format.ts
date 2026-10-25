export interface FormatOptions {
  /** 固定小数位 */
  digits?: number;
  /** 正数显示 + 号 */
  signed?: boolean;
  /** 单位；% 紧贴数字，其余以空格分隔 */
  unit?: string;
  /** 千分位 */
  group?: boolean;
}

export const EMPTY_PLACEHOLDER = "—";

export type NumericInput = string | number | null | undefined;

/** 将输入解析为有限数字；空值返回 null，非法值抛错 */
export function parseNumeric(value: NumericInput): number | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "string" && value.trim() === "") return null;
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) {
    throw new Error(`无法格式化数值: ${String(value)}`);
  }
  return n;
}

/**
 * 仅用于展示的数值格式化。
 * 资金、价格、数量在契约中是 Decimal 字符串，这里不做任何业务计算。
 */
export function formatNumber(value: NumericInput, options: FormatOptions = {}): string {
  const { digits, signed = false, unit, group = false } = options;
  if (digits !== undefined && (!Number.isInteger(digits) || digits < 0 || digits > 20)) {
    throw new RangeError(`digits 必须是 0~20 的整数: ${digits}`);
  }
  const n = parseNumeric(value);
  if (n === null) return EMPTY_PLACEHOLDER;

  let body = digits === undefined ? String(Math.abs(n)) : Math.abs(n).toFixed(digits);
  const isZero = Number(body) === 0;
  if (group) {
    const [int, frac] = body.split(".");
    const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    body = frac === undefined ? grouped : `${grouped}.${frac}`;
  }
  let sign = "";
  if (!isZero) sign = n < 0 ? "-" : signed ? "+" : "";
  const suffix = unit ? (unit === "%" ? unit : ` ${unit}`) : "";
  return `${sign}${body}${suffix}`;
}

const TIME_ZONE = "Asia/Shanghai";

const formatter = new Intl.DateTimeFormat("en-GB", {
  timeZone: TIME_ZONE,
  hourCycle: "h23",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

function assertTimestamp(tsMs: number): void {
  if (!Number.isSafeInteger(tsMs) || tsMs < 0) {
    throw new Error(`无效的时间戳（需为 UTC 毫秒非负整数）: ${String(tsMs)}`);
  }
}

function parts(tsMs: number): Record<string, string> {
  assertTimestamp(tsMs);
  const out: Record<string, string> = {};
  for (const p of formatter.formatToParts(new Date(tsMs))) out[p.type] = p.value;
  return out;
}

/** UTC 毫秒 → 北京时间 `MM-DD HH:mm`（界面统一格式，§17.2） */
export function formatBeijingTime(tsMs: number): string {
  const p = parts(tsMs);
  return `${p.month}-${p.day} ${p.hour}:${p.minute}`;
}

/** UTC 毫秒 → 北京时间 `MM-DD HH:mm:ss`（顶栏时钟） */
export function formatBeijingClock(tsMs: number): string {
  const p = parts(tsMs);
  return `${p.month}-${p.day} ${p.hour}:${p.minute}:${p.second}`;
}

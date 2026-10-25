export type DotStatus = "connected" | "connecting" | "error" | "idle";

const LABELS: Record<DotStatus, string> = {
  connected: "已连接",
  connecting: "连接中",
  error: "异常",
  idle: "未连接",
};

/** 6px 状态点；“连接中”带 1.6s 呼吸动画（减弱动效下关闭） */
export function StatusDot({ status }: { status: DotStatus }) {
  const label = LABELS[status];
  if (label === undefined) {
    throw new Error(`未知连接状态: ${String(status)}`);
  }
  return <span className="status-dot" role="img" aria-label={label} data-status={status} />;
}

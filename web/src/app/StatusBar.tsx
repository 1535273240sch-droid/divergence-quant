import { Link } from "react-router-dom";
import { StatusDot, type DotStatus } from "../ui";
import { RISK_LABELS, useAppStore } from "./store";
import { APP_VERSION } from "./version";

const CONNECTION_LABELS: Record<DotStatus, string> = {
  connected: "行情已连接",
  connecting: "行情连接中",
  error: "行情异常",
  idle: "行情未连接",
};

/** 状态栏：连接、风控、版本；异常项变语义色并可点击跳转 */
export function StatusBar() {
  const connection = useAppStore((s) => s.connection);
  const risk = useAppStore((s) => s.risk);

  const riskText = `风控 ${RISK_LABELS[risk]}`;
  return (
    <footer className="statusbar" role="contentinfo">
      <span className="statusbar-item">
        <StatusDot status={connection} />
        {CONNECTION_LABELS[connection]}
      </span>
      {risk === "normal" ? (
        <span className="statusbar-item">{riskText}</span>
      ) : (
        <Link to="/risk" className="statusbar-item statusbar-alert" data-risk={risk}>
          {riskText}
        </Link>
      )}
      <span className="statusbar-item statusbar-version num">{APP_VERSION}</span>
    </footer>
  );
}

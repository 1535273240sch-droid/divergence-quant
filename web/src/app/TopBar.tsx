import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { ModeBadge, StatusDot } from "../ui";
import { NAV_ITEMS } from "./routes";
import { useAppStore } from "./store";
import { formatBeijingClock } from "./time";

function useClock(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return now;
}

/** 顶栏：字标、主导航、命令面板入口、模式徽标、连接状态、北京时间 */
export function TopBar() {
  const mode = useAppStore((s) => s.mode);
  const connection = useAppStore((s) => s.connection);
  const latencyMs = useAppStore((s) => s.latencyMs);
  const openPalette = useAppStore((s) => s.openPalette);
  const now = useClock();

  return (
    <header className="topbar" role="banner" data-mode={mode}>
      <span className="wordmark" aria-label="DivergenceQuant">
        DQ
      </span>
      <nav className="topbar-nav" aria-label="主导航">
        {NAV_ITEMS.map((item) => (
          <NavLink key={item.path} to={item.path} end={item.path === "/"} className="topbar-link">
            {item.label}
          </NavLink>
        ))}
      </nav>
      <div className="topbar-right">
        <button type="button" className="palette-trigger" onClick={openPalette} aria-label="搜索或打开命令面板">
          <span>搜索</span>
          <kbd>Ctrl K</kbd>
        </button>
        <ModeBadge mode={mode} />
        <span className="topbar-conn num">
          <StatusDot status={connection} />
          {latencyMs === null ? "—" : `${latencyMs}ms`}
        </span>
        <time className="topbar-clock num">{formatBeijingClock(now)}</time>
      </div>
    </header>
  );
}

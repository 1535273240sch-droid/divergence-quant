import { NavLink } from "react-router-dom";
import { MOBILE_TABS } from "./routes";
import { useAppStore } from "./store";

/** 手机底部标签栏（<768px 显示）：触控目标 ≥44px，适配安全区 */
export function MobileTabBar() {
  const pending = useAppStore((s) => s.pendingConfirmCount);
  return (
    <nav className="tabbar" aria-label="底部标签栏">
      {MOBILE_TABS.map((tab) => (
        <NavLink key={tab.label} to={tab.path} end className="tabbar-link">
          {tab.label}
          {tab.path === "/confirm" && pending > 0 ? <span className="tabbar-badge num">{pending}</span> : null}
        </NavLink>
      ))}
    </nav>
  );
}

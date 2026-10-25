import { Outlet } from "react-router-dom";
import { CommandPalette } from "./CommandPalette";
import { MobileTabBar } from "./MobileTabBar";
import { StatusBar } from "./StatusBar";
import { TopBar } from "./TopBar";
import { useHotkeys } from "./useHotkeys";

/** 应用框架：顶栏 / 内容区 / 状态栏，手机端追加底部标签栏 */
export function AppShell() {
  useHotkeys();
  return (
    <div className="shell">
      <TopBar />
      <main className="shell-main">
        <Outlet />
      </main>
      <StatusBar />
      <MobileTabBar />
      <CommandPalette />
    </div>
  );
}

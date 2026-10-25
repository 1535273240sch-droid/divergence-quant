import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { resolveGotoKey } from "./routes";
import { useAppStore } from "./store";

/** G 序列的有效时长（毫秒） */
export const GOTO_WINDOW_MS = 1000;

function isEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}

/**
 * 全局快捷键（§17.12）：Ctrl/⌘+K、G→O/S/W/P/R、D、Esc。
 * 在输入框内、带修饰键、或命令面板打开时，单键快捷键一律不触发。
 */
export function useHotkeys(): void {
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    let gotoAt: number | null = null;

    function onKeyDown(event: KeyboardEvent) {
      const store = useAppStore.getState();
      const key = event.key;

      if ((event.ctrlKey || event.metaKey) && key.toLowerCase() === "k") {
        event.preventDefault();
        store.togglePalette();
        gotoAt = null;
        return;
      }
      if (key === "Escape") {
        if (store.paletteOpen) store.closePalette();
        gotoAt = null;
        return;
      }
      if (store.paletteOpen) return;
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      if (isEditable(event.target)) return;

      if (gotoAt !== null) {
        const fresh = performance.now() - gotoAt <= GOTO_WINDOW_MS;
        gotoAt = null;
        const path = fresh ? resolveGotoKey(key) : null;
        if (path) {
          event.preventDefault();
          navigate(path);
        }
        return;
      }
      if (key.toLowerCase() === "g") {
        gotoAt = performance.now();
        return;
      }
      if (key.toLowerCase() === "d") {
        store.toggleDensity();
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [navigate, location.pathname]);
}

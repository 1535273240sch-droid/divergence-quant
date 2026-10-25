import { useEffect } from "react";
import { Command } from "cmdk";
import { useNavigate } from "react-router-dom";
import { PAGES, GOTO_KEYS } from "./routes";
import { useAppStore } from "./store";

const SHORTCUT_BY_PATH: Record<string, string> = Object.fromEntries(
  Object.entries(GOTO_KEYS).map(([key, path]) => [path, `G ${key.toUpperCase()}`]),
);

/** 命令面板（⌘K / Ctrl+K）：页面跳转与全局命令。浮层使用毛玻璃（§17.11） */
export function CommandPalette() {
  const open = useAppStore((s) => s.paletteOpen);
  const close = useAppStore((s) => s.closePalette);
  const toggleDensity = useAppStore((s) => s.toggleDensity);
  const navigate = useNavigate();

  // 关闭后把焦点还给打开前的元素
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    return () => previous?.focus();
  }, [open]);

  if (!open) return null;

  function go(path: string) {
    navigate(path);
    close();
  }

  return (
    <div
      className="palette-overlay"
      data-testid="palette-overlay"
      onClick={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <div className="glass palette" role="dialog" aria-modal="true" aria-label="命令面板">
        <Command label="命令面板" loop>
          <Command.Input className="palette-input" placeholder="搜索页面或命令" autoFocus />
          <Command.List className="palette-list">
            <Command.Empty className="palette-empty">没有匹配的命令</Command.Empty>
            <Command.Group heading="页面">
              {PAGES.map((page) => (
                <Command.Item key={page.path} value={page.title} onSelect={() => go(page.path)}>
                  <span>{page.title}</span>
                  {SHORTCUT_BY_PATH[page.path] ? <kbd>{SHORTCUT_BY_PATH[page.path]}</kbd> : null}
                </Command.Item>
              ))}
            </Command.Group>
            <Command.Group heading="命令">
              <Command.Item
                value="切换密度（紧凑/舒适）"
                onSelect={() => {
                  toggleDensity();
                  close();
                }}
              >
                <span>切换密度（紧凑/舒适）</span>
                <kbd>D</kbd>
              </Command.Item>
            </Command.Group>
          </Command.List>
        </Command>
      </div>
    </div>
  );
}

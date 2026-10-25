export type Density = "compact" | "comfortable";

export const DENSITY_STORAGE_KEY = "dquant.density";
const DEFAULT_DENSITY: Density = "compact";
const DENSITIES: readonly Density[] = ["compact", "comfortable"];

function isDensity(value: unknown): value is Density {
  return typeof value === "string" && (DENSITIES as readonly string[]).includes(value);
}

/** 读取已保存的密度；存储不可用或数据脏时回退为默认紧凑 */
export function loadDensity(): Density {
  try {
    const saved = localStorage.getItem(DENSITY_STORAGE_KEY);
    return isDensity(saved) ? saved : DEFAULT_DENSITY;
  } catch {
    return DEFAULT_DENSITY;
  }
}

/** 应用密度到根节点（驱动 tokens.css 中的 data-density 变量）并尽力持久化 */
export function applyDensity(density: Density): void {
  if (!isDensity(density)) {
    throw new Error(`未知密度: ${String(density)}`);
  }
  document.documentElement.dataset.density = density;
  try {
    localStorage.setItem(DENSITY_STORAGE_KEY, density);
  } catch {
    // 持久化失败不影响本次会话的显示效果
  }
}

/** 在紧凑/舒适之间切换，返回切换后的值 */
export function toggleDensity(): Density {
  const current = document.documentElement.dataset.density;
  const base: Density = isDensity(current) ? current : loadDensity();
  const next: Density = base === "compact" ? "comfortable" : "compact";
  applyDensity(next);
  return next;
}

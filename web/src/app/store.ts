import { create } from "zustand";
import type { DotStatus, TradingMode } from "../ui";
import { type Density, applyDensity, loadDensity } from "../ui/density";

export type RiskStatus = "normal" | "restricted" | "halted";

export const RISK_LABELS: Record<RiskStatus, string> = {
  normal: "正常",
  restricted: "限制开仓",
  halted: "熔断",
};

const MODES: readonly TradingMode[] = ["paper", "live"];
const CONNECTIONS: readonly DotStatus[] = ["connected", "connecting", "error", "idle"];

export interface AppState {
  mode: TradingMode;
  density: Density;
  connection: DotStatus;
  latencyMs: number | null;
  risk: RiskStatus;
  pendingConfirmCount: number;
  paletteOpen: boolean;
}

export interface AppActions {
  setMode: (mode: TradingMode) => void;
  setDensity: (density: Density) => void;
  toggleDensity: () => void;
  setConnection: (status: DotStatus, latencyMs?: number) => void;
  setRisk: (risk: RiskStatus) => void;
  setPendingConfirmCount: (count: number) => void;
  openPalette: () => void;
  closePalette: () => void;
  togglePalette: () => void;
}

export function initialAppState(): AppState {
  return {
    mode: "paper",
    density: loadDensity(),
    connection: "idle",
    latencyMs: null,
    risk: "normal",
    pendingConfirmCount: 0,
    paletteOpen: false,
  };
}

/**
 * 全局轻量状态（模式、密度、连接、风控、命令面板）。
 * 这些值最终由 M11 API/WebSocket 推送；前端只展示，不在此复制风控规则。
 */
export const useAppStore = create<AppState & AppActions>((set, get) => ({
  ...initialAppState(),

  setMode: (mode) => {
    if (!MODES.includes(mode)) throw new Error(`未知交易模式: ${String(mode)}`);
    set({ mode });
  },

  setDensity: (density) => {
    applyDensity(density);
    set({ density });
  },

  toggleDensity: () => {
    get().setDensity(get().density === "compact" ? "comfortable" : "compact");
  },

  setConnection: (status, latencyMs) => {
    if (!CONNECTIONS.includes(status)) throw new Error(`未知连接状态: ${String(status)}`);
    if (latencyMs !== undefined && (!Number.isInteger(latencyMs) || latencyMs < 0)) {
      throw new Error(`延迟必须是非负整数毫秒: ${String(latencyMs)}`);
    }
    set({ connection: status, latencyMs: status === "connected" ? (latencyMs ?? null) : null });
  },

  setRisk: (risk) => {
    if (!Object.hasOwn(RISK_LABELS, risk)) throw new Error(`未知风控状态: ${String(risk)}`);
    set({ risk });
  },

  setPendingConfirmCount: (count) => {
    if (!Number.isInteger(count) || count < 0) {
      throw new Error(`待确认数量必须是非负整数: ${String(count)}`);
    }
    set({ pendingConfirmCount: count });
  },

  openPalette: () => set({ paletteOpen: true }),
  closePalette: () => set({ paletteOpen: false }),
  togglePalette: () => set({ paletteOpen: !get().paletteOpen }),
}));

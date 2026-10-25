/**
 * DivergenceQuant Exchange-Grade Frontend Engine (M12)
 * Zero-crash architecture, 60fps requestAnimationFrame ticker, two-stage K-line divergence rendering.
 */
(() => {
  "use strict";

  // Global Crash Shield: guarantee UI never crashes on any runtime or async error
  window.addEventListener("error", (ev) => {
    console.warn("[DQ SafeGuard Caught Error]:", ev.message);
  });
  window.addEventListener("unhandledrejection", (ev) => {
    console.warn("[DQ SafeGuard Unhandled Rejection]:", ev.reason);
  });

  function safeExec(fn, fallback = null) {
    try {
      return fn();
    } catch (err) {
      console.warn("[DQ SafeExec]:", err);
      return fallback;
    }
  }

  // ==================== 1. STATE & REALISTIC MARKET DATASET ====================
  const state = {
    view: "overview",
    mode: "demo", // 'demo' | 'live'
    density: "compact",
    haltNewOrders: false,
    overviewPool: "ALL",
    screenerPool: "ALL",
    screenerMa: "SMA",
    screenerSearch: "",
    screenerTrends: new Set(["上涨", "下跌", "震荡"]),
    screenerDivs: new Set(["底背离", "顶背离", "无"]),
    screenerMinScore: 60,
    screenerDiffOnly: false,
    screenerOrder2Only: false,
    screenerEarlyOnly: false,
    screenerSortKey: "score",
    screenerSortAsc: false,
    selectedScreenerSymbol: "SOL",

    wsPool: "ALL",
    wsSearch: "",
    activeSymbol: "SOL",
    timeframe: "4H",
    showSma: true,
    showEma: true,
    showPlan: true,
    annotateMode: false,
    annotateClicks: [],
    activeDockTab: "positions",
    activePortfolioTab: "positions",

    equityRange: "7D",
    showBench: true,

    logLevel: "ALL",
    logAutoScroll: true,
    scanCountdownSec: 14052,
    heartbeatAge: 1,
    proposalStatus: "PENDING", // 'PENDING' | 'APPROVED' | 'REJECTED'
  };

  const candidates = [
    {
      symbol: "SOL", price: 182.35, decimals: 2, change: 3.20, volText: "1.24B", volNum: 1240,
      pool: "A", sma: "上涨", ema: "上涨", dist: 8.1, slope: 2.3,
      divergence: "底背离", divKind: "BULL_REGULAR", divOrder: 2, early: false,
      heightOk: true, areaOk: true, volOk: true, score: 82, time: "16:00",
      high24: 185.40, low24: 176.02, funding: "+0.0100%",
      seg1Price: 178.40, seg2Price: 176.02, seg1Peak: -1.82, seg2Peak: -0.94,
      seg1Area: 21.4, seg2Area: 9.8, volRatio: 0.62,
      entry: 183.20, stop: 174.80, tp1: 195.80, tp2: 200.00, size: "1.80",
      spark: [176, 178, 177, 179, 176.2, 178.5, 180.1, 181.4, 182.35],
      aiMod: "+8", aiStatus: "形态干净 · 消息面安全放行",
      aiBullets: [
        "视觉二审：G2 绿柱快速收敛，针尖精准回踩前低支撑区。",
        "宏观避雷：近 24h 无大额解锁或监管利空，资金费率中性偏多。",
        "执行建议：1H 收盘已站稳枢轴高点，建议 S1 标准仓位执行。"
      ],
      scoreBreakdown: [23, 18, 13, 13, 9, 6]
    },
    {
      symbol: "ARB", price: 0.8123, decimals: 4, change: -1.42, volText: "320M", volNum: 320,
      pool: "B", sma: "下跌", ema: "下跌", dist: -12.4, slope: -1.8,
      divergence: "顶背离", divKind: "BEAR_REGULAR", divOrder: 1, early: false,
      heightOk: true, areaOk: true, volOk: false, score: 76, time: "12:00",
      high24: 0.8390, low24: 0.8010, funding: "-0.0045%",
      seg1Price: 0.8280, seg2Price: 0.8390, seg1Peak: 1.45, seg2Peak: 0.88,
      seg1Area: 18.2, seg2Area: 10.4, volRatio: 0.84,
      entry: 0.8080, stop: 0.8420, tp1: 0.7570, tp2: 0.7400, size: "220",
      spark: [0.83, 0.838, 0.825, 0.839, 0.828, 0.821, 0.818, 0.815, 0.8123],
      aiMod: "+4", aiStatus: "顺势反抽衰竭 · 适合高空",
      aiBullets: [
        "视觉二审：日线受制于 MA200 下方，4H 两段红柱面积缩减 43%。",
        "消息避雷：下周有常规生态激励释放，空头趋势未改。",
        "执行建议：等待 1H 跌破 0.8080 颈线位顺势做空。"
      ],
      scoreBreakdown: [20, 16, 12, 10, 9, 9]
    },
    {
      symbol: "DOGE", price: 0.1523, decimals: 4, change: 0.84, volText: "488M", volNum: 488,
      pool: "C", sma: "震荡", ema: "上涨", dist: 2.0, slope: 0.4,
      divergence: "底背离", divKind: "BULL_REGULAR", divOrder: 1, early: false,
      heightOk: true, areaOk: true, volOk: true, score: 71, time: "12:00",
      high24: 0.1560, low24: 0.1482, funding: "+0.0082%",
      seg1Price: 0.1495, seg2Price: 0.1482, seg1Peak: -0.92, seg2Peak: -0.51,
      seg1Area: 14.6, seg2Area: 7.9, volRatio: 0.68,
      entry: 0.1530, stop: 0.1468, tp1: 0.1623, tp2: 0.1654, size: "820",
      spark: [0.151, 0.149, 0.152, 0.1482, 0.150, 0.151, 0.1518, 0.152, 0.1523],
      aiMod: "+3", aiStatus: "SMA/EMA 轻微震荡 · 半仓低吸",
      aiBullets: [
        "视觉二审：处于 50 根 4H 箱体下沿 0.5×ATR 缓冲带，底背离结构清晰。",
        "趋势对照：SMA200 判定震荡，EMA200 判定上涨，存在均线分歧。",
        "执行建议：按 C 池 S2 震荡反转策略半仓（50%）参与。"
      ],
      scoreBreakdown: [19, 15, 11, 12, 8, 6]
    },
    {
      symbol: "APT", price: 9.842, decimals: 3, change: 2.15, volText: "215M", volNum: 215,
      pool: "A", sma: "上涨", ema: "上涨", dist: 6.4, slope: 1.5,
      divergence: "底背离", divKind: "BULL_REGULAR", divOrder: 1, early: false,
      heightOk: true, areaOk: true, volOk: true, score: 69, time: "08:00",
      high24: 10.120, low24: 9.480, funding: "+0.0060%",
      seg1Price: 9.560, seg2Price: 9.480, seg1Peak: -1.15, seg2Peak: -0.68,
      seg1Area: 16.0, seg2Area: 8.8, volRatio: 0.71,
      entry: 9.900, stop: 9.420, tp1: 10.620, tp2: 10.860, size: "18",
      spark: [9.6, 9.55, 9.7, 9.48, 9.62, 9.71, 9.78, 9.81, 9.842],
      aiMod: "+5", aiStatus: "上升通道回踩 · 结构稳健",
      aiBullets: [
        "视觉二审：日线连续 18 根稳居 MA200 之上，4H 回调缩量明显。",
        "消息避雷：链上 TVL 创近期新高，无负面事件。",
        "执行建议：突破 9.90 触发 S1 顺势多单。"
      ],
      scoreBreakdown: [18, 15, 11, 12, 7, 6]
    },
    {
      symbol: "SUI", price: 1.924, decimals: 3, change: 4.72, volText: "390M", volNum: 390,
      pool: "A", sma: "上涨", ema: "上涨", dist: 9.8, slope: 2.8,
      divergence: "底背离", divKind: "BULL_REGULAR", divOrder: 2, early: false,
      heightOk: true, areaOk: true, volOk: true, score: 85, time: "16:00",
      high24: 1.965, low24: 1.812, funding: "+0.0125%",
      seg1Price: 1.840, seg2Price: 1.812, seg1Peak: -1.64, seg2Peak: -0.79,
      seg1Area: 24.1, seg2Area: 10.2, volRatio: 0.58,
      entry: 1.935, stop: 1.805, tp1: 2.130, tp2: 2.195, size: "95",
      spark: [1.84, 1.86, 1.812, 1.85, 1.88, 1.89, 1.91, 1.918, 1.924],
      aiMod: "+9", aiStatus: "二次底背离共振 · 极佳做多点",
      aiBullets: [
        "视觉二审：走出 G0→G1→G2 标准二次底背离（+15分），面积缩减 57%。",
        "多周期共振：1H 级别同步出现底背离且已放量突破颈线。",
        "执行建议：A 池最高分标的，优先分配仓位。"
      ],
      scoreBreakdown: [24, 19, 14, 14, 8, 6]
    },
    {
      symbol: "ETH", price: 3452.80, decimals: 2, change: 1.84, volText: "1.85B", volNum: 1850,
      pool: "A", sma: "上涨", ema: "上涨", dist: 5.2, slope: 1.4,
      divergence: "底背离", divKind: "BULL_REGULAR", divOrder: 1, early: false,
      heightOk: true, areaOk: true, volOk: true, score: 78, time: "16:00",
      high24: 3488.00, low24: 3375.50, funding: "+0.0095%",
      seg1Price: 3392.00, seg2Price: 3375.50, seg1Peak: -14.2, seg2Peak: -7.8,
      seg1Area: 112.0, seg2Area: 54.0, volRatio: 0.66,
      entry: 3460.00, stop: 3368.00, tp1: 3598.00, tp2: 3644.00, size: "0.42",
      spark: [3390, 3410, 3375.5, 3402, 3420, 3435, 3441, 3448, 3452.8],
      aiMod: "+6", aiStatus: "主流龙头稳健 · 已持仓保护中",
      aiBullets: [
        "视觉二审：日线 MA200 支撑强劲，4H 绿柱面积腰斩。",
        "持仓状态：当前账户已持有 0.42 张多单，浮盈 +32.11 USDT。",
        "执行建议：维持现有仓位，触及 1.5R 自动平半仓。"
      ],
      scoreBreakdown: [21, 17, 13, 13, 8, 6]
    },
    {
      symbol: "BTC", price: 68018.0, decimals: 1, change: -0.59, volText: "3.42B", volNum: 3420,
      pool: "E", sma: "上涨", ema: "上涨", dist: 4.2, slope: 1.1,
      divergence: "顶背离", divKind: "BEAR_REGULAR", divOrder: 1, early: false,
      heightOk: true, areaOk: true, volOk: true, score: 74, time: "12:00",
      high24: 68850.0, low24: 67420.0, funding: "+0.0110%",
      seg1Price: 68420.0, seg2Price: 68850.0, seg1Peak: 185.0, seg2Peak: 96.0,
      seg1Area: 980.0, seg2Area: 460.0, volRatio: 0.69,
      entry: 67900.0, stop: 69680.0, tp1: 65230.0, tp2: 64340.0, size: "0.08",
      spark: [68420, 68100, 68850, 68500, 68320, 68190, 68090, 68050, 68018],
      aiMod: "+2", aiStatus: "4H 顶背离观察期 · 逆势轻仓",
      aiBullets: [
        "大盘联动：BTC 处于日线上涨中的 4H 顶背离，触发山寨多单大盘系数折算。",
        "风控约束：属于 E 逆势池，仓位强制乘以 0.30 池系数。",
        "执行建议：小仓位试空已入场，严格带好 69,680 保护止损。"
      ],
      scoreBreakdown: [20, 16, 12, 12, 8, 6]
    },
    {
      symbol: "INJ", price: 22.64, decimals: 2, change: -2.18, volText: "164M", volNum: 164,
      pool: "B", sma: "下跌", ema: "下跌", dist: -9.5, slope: -1.6,
      divergence: "顶背离", divKind: "BEAR_REGULAR", divOrder: 1, early: false,
      heightOk: true, areaOk: true, volOk: true, score: 73, time: "12:00",
      high24: 23.45, low24: 22.10, funding: "-0.0020%",
      seg1Price: 23.10, seg2Price: 23.45, seg1Peak: 0.42, seg2Peak: 0.24,
      seg1Area: 6.8, seg2Area: 3.7, volRatio: 0.65,
      entry: 22.50, stop: 23.60, tp1: 20.85, tp2: 20.30, size: "11",
      spark: [23.1, 22.8, 23.45, 23.2, 22.9, 22.8, 22.75, 22.68, 22.64],
      aiMod: "+5", aiStatus: "空头排列反弹衰竭 · 顺势做空",
      aiBullets: [
        "视觉二审：反弹受阻于 EMA200，4H 顶背离缩量明显。",
        "消息避雷：无异常突发事件。",
        "执行建议：B 池顺势空单候选。"
      ],
      scoreBreakdown: [19, 16, 12, 12, 8, 6]
    },
    {
      symbol: "OP", price: 1.742, decimals: 3, change: 1.12, volText: "198M", volNum: 198,
      pool: "C", sma: "震荡", ema: "震荡", dist: -1.2, slope: 0.2,
      divergence: "底背离", divKind: "BULL_REGULAR", divOrder: 1, early: false,
      heightOk: true, areaOk: true, volOk: false, score: 66, time: "08:00",
      high24: 1.780, low24: 1.695, funding: "+0.0050%",
      seg1Price: 1.710, seg2Price: 1.695, seg1Peak: -0.045, seg2Peak: -0.028,
      seg1Area: 0.62, seg2Area: 0.39, volRatio: 0.82,
      entry: 1.750, stop: 1.685, tp1: 1.848, tp2: 1.880, size: "65",
      spark: [1.72, 1.71, 1.695, 1.71, 1.72, 1.73, 1.735, 1.74, 1.742],
      aiMod: "+1", aiStatus: "区间下沿支撑有效 · 缩量略弱",
      aiBullets: [
        "视觉二审：箱体下沿底背离，但 G2 缩量比 0.82 略高于理想值。",
        "执行建议：仅限 C 池半仓轻度参与。"
      ],
      scoreBreakdown: [17, 14, 11, 9, 8, 7]
    },
    {
      symbol: "AVAX", price: 27.85, decimals: 2, change: -0.92, volText: "275M", volNum: 275,
      pool: "D", sma: "震荡", ema: "下跌", dist: -2.4, slope: -0.5,
      divergence: "顶背离", divKind: "BEAR_REGULAR", divOrder: 1, early: false,
      heightOk: true, areaOk: true, volOk: true, score: 68, time: "08:00",
      high24: 28.40, low24: 27.30, funding: "+0.0030%",
      seg1Price: 28.15, seg2Price: 28.40, seg1Peak: 0.55, seg2Peak: 0.31,
      seg1Area: 8.4, seg2Area: 4.5, volRatio: 0.70,
      entry: 27.70, stop: 28.55, tp1: 26.42, tp2: 26.00, size: "8",
      spark: [28.1, 27.9, 28.4, 28.2, 28.05, 27.95, 27.9, 27.88, 27.85],
      aiMod: "+3", aiStatus: "震荡箱体上沿顶背离 · 半仓高抛",
      aiBullets: [
        "趋势对照：SMA200 震荡，EMA200 偏空，箱体上沿阻力明显。",
        "执行建议：S2 策略半仓高抛。"
      ],
      scoreBreakdown: [18, 15, 11, 11, 7, 6]
    },
    {
      symbol: "LINK", price: 11.92, decimals: 2, change: 1.65, volText: "182M", volNum: 182,
      pool: "C", sma: "震荡", ema: "上涨", dist: 1.8, slope: 0.6,
      divergence: "底背离", divKind: "BULL_REGULAR", divOrder: 1, early: true,
      heightOk: true, areaOk: true, volOk: true, score: 64, time: "未收盘",
      high24: 12.15, low24: 11.58, funding: "+0.0075%",
      seg1Price: 11.68, seg2Price: 11.58, seg1Peak: -0.22, seg2Peak: -0.13,
      seg1Area: 3.1, seg2Area: 1.7, volRatio: 0.67,
      entry: 12.00, stop: 11.50, tp1: 12.75, tp2: 13.00, size: "20",
      spark: [11.7, 11.68, 11.8, 11.58, 11.72, 11.81, 11.86, 11.9, 11.92],
      aiMod: "0", aiStatus: "早期提示 (Preliminary) · 等待 4H 收盘确认",
      aiBullets: [
        "防未来函数提醒：当前 G2 绿柱连续 3 根缩短但尚未转红确认，仅供盯盘提醒，禁止自动下单。"
      ],
      scoreBreakdown: [17, 14, 10, 11, 6, 6]
    },
    {
      symbol: "WIF", price: 2.548, decimals: 3, change: -3.45, volText: "410M", volNum: 410,
      pool: "E", sma: "上涨", ema: "上涨", dist: 11.2, slope: 3.1,
      divergence: "顶背离", divKind: "BEAR_REGULAR", divOrder: 2, early: false,
      heightOk: true, areaOk: true, volOk: true, score: 79, time: "16:00",
      high24: 2.680, low24: 2.490, funding: "+0.0180%",
      seg1Price: 2.620, seg2Price: 2.680, seg1Peak: 0.19, seg2Peak: 0.09,
      seg1Area: 2.8, seg2Area: 1.2, volRatio: 0.61,
      entry: 2.520, stop: 2.695, tp1: 2.258, tp2: 2.170, size: "35",
      spark: [2.62, 2.58, 2.68, 2.64, 2.61, 2.59, 2.57, 2.555, 2.548],
      aiMod: "+4", aiStatus: "高位二次顶背离 · 仅限 E 池 30% 轻仓",
      aiBullets: [
        "视觉二审：Meme 板块高波动，4H 二次顶背离结构完整。",
        "风控门禁：当前逆势持仓槽位已满 (1/1)，需等 BTC 空单平仓后方可入场。"
      ],
      scoreBreakdown: [22, 18, 13, 13, 8, 5]
    },
    {
      symbol: "NEAR", price: 4.865, decimals: 3, change: 1.92, volText: "156M", volNum: 156,
      pool: "F", sma: "上涨", ema: "上涨", dist: 4.8, slope: 1.2,
      divergence: "无", divKind: "NONE", divOrder: 0, early: false,
      heightOk: false, areaOk: false, volOk: false, score: 52, time: "—",
      high24: 4.920, low24: 4.710, funding: "+0.0065%",
      seg1Price: 4.75, seg2Price: 4.72, seg1Peak: -0.08, seg2Peak: -0.09,
      seg1Area: 1.2, seg2Area: 1.1, volRatio: 0.95,
      entry: 4.90, stop: 4.70, tp1: 5.20, tp2: 5.30, size: "0",
      spark: [4.75, 4.78, 4.81, 4.79, 4.82, 4.84, 4.85, 4.86, 4.865],
      aiMod: "0", aiStatus: "纯趋势跟踪 · 暂无背离结构",
      aiBullets: ["当前处于稳健上升趋势，未出现顶底背离，仅展示在 F 池。"],
      scoreBreakdown: [10, 10, 10, 8, 8, 6]
    },
    {
      symbol: "RENDER", price: 5.412, decimals: 3, change: 2.64, volText: "142M", volNum: 142,
      pool: "F", sma: "上涨", ema: "上涨", dist: 7.1, slope: 1.9,
      divergence: "无", divKind: "NONE", divOrder: 0, early: false,
      heightOk: false, areaOk: false, volOk: false, score: 55, time: "—",
      high24: 5.510, low24: 5.220, funding: "+0.0080%",
      seg1Price: 5.25, seg2Price: 5.22, seg1Peak: -0.11, seg2Peak: -0.11,
      seg1Area: 1.5, seg2Area: 1.4, volRatio: 0.92,
      entry: 5.45, stop: 5.20, tp1: 5.82, tp2: 5.95, size: "0",
      spark: [5.25, 5.28, 5.31, 5.35, 5.33, 5.38, 5.39, 5.40, 5.412],
      aiMod: "0", aiStatus: "AI 板块多头趋势 · 等待回调背离",
      aiBullets: ["日线趋势多头排列，等待 4H 出现回调底背离后再介入。"],
      scoreBreakdown: [11, 11, 10, 9, 8, 6]
    },
    {
      symbol: "TIA", price: 5.128, decimals: 3, change: -1.85, volText: "138M", volNum: 138,
      pool: "F", sma: "下跌", ema: "下跌", dist: -14.2, slope: -2.4,
      divergence: "无", divKind: "NONE", divOrder: 0, early: false,
      heightOk: false, areaOk: false, volOk: false, score: 48, time: "—",
      high24: 5.310, low24: 5.060, funding: "-0.0120%",
      seg1Price: 5.28, seg2Price: 5.31, seg1Peak: 0.09, seg2Peak: 0.10,
      seg1Area: 1.4, seg2Area: 1.5, volRatio: 1.02,
      entry: 5.10, stop: 5.35, tp1: 4.72, tp2: 4.60, size: "0",
      spark: [5.28, 5.25, 5.22, 5.19, 5.21, 5.17, 5.15, 5.14, 5.128],
      aiMod: "0", aiStatus: "空头趋势延续 · 暂无反弹背离",
      aiBullets: ["空头趋势中，等待反弹产生 4H 顶背离。"],
      scoreBreakdown: [9, 9, 9, 8, 7, 6]
    },
    {
      symbol: "AAVE", price: 154.60, decimals: 2, change: 0.95, volText: "128M", volNum: 128,
      pool: "F", sma: "上涨", ema: "震荡", dist: 3.1, slope: 0.9,
      divergence: "无", divKind: "NONE", divOrder: 0, early: false,
      heightOk: false, areaOk: false, volOk: false, score: 54, time: "—",
      high24: 157.20, low24: 151.80, funding: "+0.0055%",
      seg1Price: 152.0, seg2Price: 151.8, seg1Peak: -1.1, seg2Peak: -1.0,
      seg1Area: 12.0, seg2Area: 11.2, volRatio: 0.90,
      entry: 155.0, stop: 151.0, tp1: 161.0, tp2: 163.0, size: "0",
      spark: [152.5, 153.1, 152.8, 153.6, 154.0, 153.9, 154.2, 154.4, 154.6],
      aiMod: "0", aiStatus: "SMA/EMA 存在轻微观察分歧",
      aiBullets: ["DeFi 龙头稳健运行，暂未触发背离入场条件。"],
      scoreBreakdown: [11, 10, 10, 9, 8, 6]
    }
  ];

  const positions = [
    {
      id: "pos-sol", symbol: "SOL", side: "long", lever: "3x 逐仓", size: "1.80",
      entry: 183.20, mark: 185.44, liq: 108.20, pnl: 7.24, pnlPct: 4.03,
      stop: 174.80, tp: 196.30, strategy: "S1 顺势回调", duration: "06:42:18",
      status: "4H反向背离监控中"
    },
    {
      id: "pos-eth", symbol: "ETH", side: "long", lever: "3x 逐仓", size: "0.42",
      entry: 3426.10, mark: 3452.80, liq: 2108.00, pnl: 37.78, pnlPct: 3.11,
      stop: 3278.00, tp: 3644.00, strategy: "S1 顺势回调", duration: "1d 04:12",
      status: "1R保本监控中"
    },
    {
      id: "pos-btc", symbol: "BTC", side: "short", lever: "2x 逐仓", size: "0.08",
      entry: 68420.0, mark: 68018.0, liq: 77120.0, pnl: -2.84, pnlPct: -0.59,
      stop: 69680.0, tp: 65230.0, strategy: "S3 逆势背离", duration: "02:08:44",
      status: "E池30%轻仓"
    }
  ];

  const openOrders = [
    { clOrdId: "S120261024160001", symbol: "SUI", side: "long", type: "限价突破", price: "1.935", size: "95", filled: "0%", stop: "1.805", status: "LIVE (超时60s保护)", time: "16:00:05" },
    { clOrdId: "S120261024120002", symbol: "ARB", side: "short", type: "限价突破", price: "0.8080", size: "220", filled: "0%", stop: "0.8420", status: "LIVE (超时60s保护)", time: "12:00:08" }
  ];

  const algoOrders = [
    { id: "ALG-901", symbol: "SOL", side: "平多", trigger: "Mark ≤ 174.80", tpTrigger: "Mark ≥ 196.30", size: "1.80", reduceOnly: "YES", status: "EFFECTIVE" },
    { id: "ALG-902", symbol: "ETH", side: "平多", trigger: "Mark ≤ 3,278.00", tpTrigger: "Mark ≥ 3,644.00", size: "0.42", reduceOnly: "YES", status: "EFFECTIVE" },
    { id: "ALG-903", symbol: "BTC", side: "平空", trigger: "Mark ≥ 69,680.0", tpTrigger: "Mark ≤ 65,230.0", size: "0.08", reduceOnly: "YES", status: "EFFECTIVE" }
  ];

  const fillHistory = [
    { time: "10-24 14:02:11", symbol: "SOL", side: "开多", price: "183.20", size: "1.80", fee: "-0.16 USDT", pnl: "—", strategy: "S1 顺势回调" },
    { time: "10-24 08:15:40", symbol: "AVAX", side: "平多 (1.5R止盈)", price: "28.35", size: "12.0", fee: "-0.17 USDT", pnl: "+84.60 USDT", strategy: "S1 顺势回调" },
    { time: "10-23 20:04:19", symbol: "DOGE", side: "平多 (反向背离出场)", price: "0.1548", size: "900", fee: "-0.07 USDT", pnl: "+59.62 USDT", strategy: "S2 震荡反转" },
    { time: "10-23 12:01:05", symbol: "INJ", side: "平空 (止损)", price: "23.40", size: "10.0", fee: "-0.11 USDT", pnl: "-14.80 USDT", strategy: "S1 顺势回调" }
  ];

  const ledgerEntries = [
    { time: "10-24 16:00:00", type: "资金费率结算", symbol: "SOL-USDT-SWAP", amount: "-0.32 USDT", balance: "12,480.52 USDT" },
    { time: "10-24 16:00:00", type: "资金费率结算", symbol: "ETH-USDT-SWAP", amount: "-0.41 USDT", balance: "12,480.84 USDT" },
    { time: "10-24 08:15:40", type: "已实现平仓盈亏", symbol: "AVAX-USDT-SWAP", amount: "+84.60 USDT", balance: "12,481.25 USDT" },
    { time: "10-23 20:04:19", type: "已实现平仓盈亏", symbol: "DOGE-USDT-SWAP", amount: "+59.62 USDT", balance: "12,396.65 USDT" }
  ];

  const pendingConfirms = [
    {
      id: "cfm-doge", symbol: "DOGE", pool: "C 震荡低吸", side: "开多", score: 71,
      signalPrice: 0.1520, currentPrice: 0.1523, slipPct: 0.20, maxSlip: 1.0,
      stop: 0.1468, tp: "0.1623 / 0.1654", size: "820 张", risk: "0.75%",
      remainSec: 698
    },
    {
      id: "cfm-sui", symbol: "SUI", pool: "A 顺势做多", side: "开多", score: 85,
      signalPrice: 1.918, currentPrice: 1.924, slipPct: 0.31, maxSlip: 1.0,
      stop: 1.805, tp: "2.130 / 2.195", size: "95 张", risk: "1.50%",
      remainSec: 812
    }
  ];

  const goldenSamples = [
    { id: "GS-01", symbol: "SOL-USDT-SWAP", bar: "4H", kind: "BULL_REGULAR", g1: "2026-10-21 08:00", g2: "2026-10-24 12:00", source: "web_ui", note: "二次底背离，柱面积缩减 54%，缩量 0.62x" },
    { id: "GS-02", symbol: "BTC-USDT-SWAP", bar: "4H", kind: "BULL_REGULAR", g1: "2026-10-14 12:00", g2: "2026-10-18 08:00", source: "web_ui", note: "两段绿柱面积明显缩小，针尖破前低" },
    { id: "GS-03", symbol: "ETH-USDT-SWAP", bar: "4H", kind: "BEAR_REGULAR", g1: "2026-10-09 04:00", g2: "2026-10-12 16:00", source: "web_ui", note: "日线阻力位标准顶背离" },
    { id: "GS-04", symbol: "SUI-USDT-SWAP", bar: "4H", kind: "BULL_REGULAR", g1: "2026-10-20 00:00", g2: "2026-10-24 16:00", source: "web_ui", note: "强势顺势回调二次底背离" },
    { id: "GS-05", symbol: "ARB-USDT-SWAP", bar: "4H", kind: "BEAR_REGULAR", g1: "2026-10-22 04:00", g2: "2026-10-24 12:00", source: "web_ui", note: "下跌中继反弹顶背离" }
  ];

  const strategiesList = [
    { code: "S1", name: "顺势回调背离", tag: "主力策略 · A/B池", on: true, weight: 60, pnl30d: "+14.8%", winRate: "61.2%", trades: 49, desc: "1D 处于上涨/下跌趋势，4H 出现同向回调背离，1H 结构突破触发。" },
    { code: "S2", name: "震荡区间背离反转", tag: "半仓策略 · C/D池", on: true, weight: 25, pnl30d: "+4.6%", winRate: "54.5%", trades: 22, desc: "1D 处于震荡区间，价格触及 50 根 4H 高低点缓冲带并出现背离。" },
    { code: "S3", name: "逆势摸顶抄底背离", tag: "高风险 · E池 (30%仓位)", on: false, weight: 15, pnl30d: "-0.8%", winRate: "45.0%", trades: 11, desc: "默认关闭；仅在二次背离且 AI 新闻避雷放行时允许 30% 轻仓参与。" }
  ];

  const liveLogs = [
    { time: "23:44:12", level: "INFO", mod: "OMS", msg: "OKX 账本对账完成：本地持仓 3 笔 == 交易所持仓 3 笔，挂单差异 0。" },
    { time: "23:43:50", level: "INFO", mod: "AI", msg: "Agent 1 (vision_model) 命中缓存：SOL-USDT 4H 形态修正分 +8 (耗时 18ms)。" },
    { time: "23:42:15", level: "WARN", mod: "RISK", msg: "周末流动性系数生效中 (Asia/Shanghai 周六/日)：新开仓基础张数自动乘以 0.70。" },
    { time: "23:40:03", level: "INFO", mod: "SCR", msg: "4H 全市场扫描完成：初筛 165 个交易对，命中候选 12 个 (A:4, B:2, C:3, D:1, E:2)，用时 38.4s。" },
    { time: "23:15:22", level: "WARN", mod: "DIV", msg: "PEPE-USDT 出现第 3 次连续底背离未反转，判定单边趋势指标钝化，写入 symbol_cooldowns 冷却 48h。" },
    { time: "22:58:10", level: "ERROR", mod: "AI", msg: "Agent 2 备用节点超时 (>12s)，已自动切换主路由 deepseek-chat 并完成新闻避雷校验。" }
  ];

  // ==================== 2. UTILITIES & TOAST / DIALOG ENGINE ====================
  function formatPrice(val, decimals = 2) {
    const num = Number(val);
    if (Number.isNaN(num)) return String(val);
    if (num >= 1000) {
      return num.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
    }
    return num.toFixed(decimals);
  }

  function buildSparklineSvg(points, isUp) {
    if (!points || points.length < 2) return "";
    const min = Math.min(...points);
    const max = Math.max(...points);
    const span = max - min || 1;
    const coords = points.map((p, i) => {
      const x = ((i / (points.length - 1)) * 60 + 2).toFixed(1);
      const y = (18 - ((p - min) / span) * 14).toFixed(1);
      return `${x},${y}`;
    });
    const stroke = isUp ? "var(--up)" : "var(--down)";
    return `<svg class="sparkline-svg" viewBox="0 0 64 20" aria-hidden="true"><polyline fill="none" stroke="${stroke}" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" points="${coords.join(" ")}" /></svg>`;
  }

  function showToast(message, type = "up") {
    safeExec(() => {
      const stack = document.getElementById("toastStack");
      if (!stack) return;
      while (stack.children.length >= 3) {
        stack.firstElementChild.remove();
      }
      const item = document.createElement("div");
      item.className = `toast-item toast-${type}`;
      const dotClass = type === "down" ? "danger" : type === "warn" ? "warn" : "live";
      item.innerHTML = `<i class="status-dot ${dotClass}"></i><span>${message}</span>`;
      stack.appendChild(item);
      window.setTimeout(() => {
        item.style.opacity = "0";
        item.style.transform = "translateY(6px)";
        item.style.transition = "opacity 180ms ease, transform 180ms ease";
        window.setTimeout(() => item.remove(), 200);
      }, 3800);
    });
  }

  // Native <dialog> helper with light-dismiss fallback (modern-web-guidance compliance)
  function setupDialogLightDismiss(dialogEl) {
    if (!dialogEl) return;
    if (!("closedBy" in HTMLDialogElement.prototype)) {
      dialogEl.addEventListener("click", (event) => {
        if (event.target !== dialogEl) return;
        const rect = dialogEl.getBoundingClientRect();
        const inside =
          rect.top <= event.clientY &&
          event.clientY <= rect.top + rect.height &&
          rect.left <= event.clientX &&
          event.clientX <= rect.left + rect.width;
        if (!inside) dialogEl.close();
      });
    }
  }

  function openActionDialog({ title, bodyHtml, footerHtml, onMount }) {
    safeExec(() => {
      const dlg = document.getElementById("actionDialog");
      if (!dlg) return;
      document.getElementById("actionDialogTitle").textContent = title;
      document.getElementById("actionDialogBody").innerHTML = bodyHtml;
      document.getElementById("actionDialogFooter").innerHTML = footerHtml;
      if (!dlg.open) dlg.showModal();
      if (typeof onMount === "function") onMount(dlg);
    });
  }

  function closeActionDialog() {
    const dlg = document.getElementById("actionDialog");
    if (dlg?.open) dlg.close();
  }

  // Radial Mouse Spotlight Effect (§17.8)
  function initSpotlightSurfaces() {
    document.querySelectorAll(".spotlight-surface").forEach((el) => {
      el.addEventListener("pointermove", (ev) => {
        const rect = el.getBoundingClientRect();
        el.style.setProperty("--spot-x", `${(ev.clientX - rect.left).toFixed(0)}px`);
        el.style.setProperty("--spot-y", `${(ev.clientY - rect.top).toFixed(0)}px`);
      });
    });
  }

  // ==================== 3. VIEW NAVIGATION & TOPBAR CONTROLS ====================
  function setView(viewName) {
    safeExec(() => {
      if (!viewName) return;
      state.view = viewName;
      document.querySelectorAll("[data-view-panel]").forEach((panel) => {
        panel.classList.toggle("is-visible", panel.dataset.viewPanel === viewName);
      });
      document.querySelectorAll(".main-nav .nav-item, .mobile-tabbar button").forEach((btn) => {
        btn.classList.toggle("is-active", btn.dataset.view === viewName);
      });
      closeScreenerDrawer();
      if (viewName === "workspace") {
        renderWorkspace();
      } else if (viewName === "screener") {
        renderScreenerTable();
      } else if (viewName === "positions") {
        renderConfirmQueue();
        renderPortfolioTable();
      } else if (viewName === "strategies") {
        renderStrategies();
      } else if (viewName === "backtest") {
        renderBacktestHeatmap();
      } else if (viewName === "risk") {
        renderLogs();
      }
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }

  function openSymbolInWorkspace(symbol) {
    const found = candidates.find((c) => c.symbol === symbol);
    if (found) {
      state.activeSymbol = found.symbol;
    }
    setView("workspace");
  }

  // ==================== 4. OVERVIEW RENDERING ====================
  const equityCurvesData = {
    "7D": {
      axis: ["10-18", "10-20", "10-22", "10-24"],
      eq: [10860, 11040, 10980, 11320, 11590, 11480, 11890, 12120, 12040, 12310, 12480.52],
      bench: [10860, 10910, 10820, 11010, 11120, 11050, 11240, 11380, 11310, 11490, 11560],
      dd: [-0.4, -0.2, -0.8, -0.3, -0.1, -1.2, -0.4, -0.2, -0.9, -0.3, -0.1]
    },
    "30D": {
      axis: ["09-25", "10-05", "10-15", "10-24"],
      eq: [9650, 9920, 9810, 10240, 10680, 10490, 10950, 11420, 11780, 12150, 12480.52],
      bench: [9650, 9780, 9690, 9910, 10120, 9980, 10240, 10510, 10720, 10890, 11040],
      dd: [-1.1, -0.5, -1.8, -0.6, -0.2, -2.4, -0.8, -0.3, -1.2, -0.5, -0.2]
    },
    "90D": {
      axis: ["07-26", "08-25", "09-24", "10-24"],
      eq: [8120, 8450, 8290, 8890, 9340, 9120, 9850, 10620, 11340, 11980, 12480.52],
      bench: [8120, 8290, 8010, 8340, 8620, 8490, 8890, 9240, 9610, 9880, 10120],
      dd: [-2.1, -0.9, -3.2, -1.1, -0.4, -2.8, -1.2, -0.6, -1.5, -0.4, -0.1]
    },
    "ALL": {
      axis: ["2025-Q4", "2026-Q1", "2026-Q2", "2026-Q4"],
      eq: [5000, 5820, 6410, 7120, 7890, 8420, 9310, 10280, 11190, 11860, 12480.52],
      bench: [5000, 5410, 5780, 6120, 6590, 6810, 7240, 7690, 8120, 8490, 8820],
      dd: [-3.2, -1.8, -2.5, -1.4, -2.9, -3.21, -1.5, -0.9, -1.8, -0.7, -0.2]
    }
  };

  function renderEquityChart() {
    safeExec(() => {
      const data = equityCurvesData[state.equityRange] || equityCurvesData["7D"];
      const vals = data.eq;
      const bench = data.bench;
      const allVals = state.showBench ? [...vals, ...bench] : vals;
      const min = Math.min(...allVals) * 0.985;
      const max = Math.max(...allVals) * 1.01;
      const span = max - min || 1;

      const toPoints = (arr) =>
        arr.map((v, i) => {
          const x = (i / (arr.length - 1)) * 900;
          const y = 205 - ((v - min) / span) * 168;
          return [x, y];
        });

      const eqPts = toPoints(vals);
      const benchPts = toPoints(bench);
      const lineD = eqPts.map((p, i) => `${i === 0 ? "M" : "L"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
      const areaD = `${lineD} L900,220 L0,220 Z`;
      const benchD = benchPts.map((p, i) => `${i === 0 ? "M" : "L"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");

      const ddPts = data.dd.map((d, i) => {
        const x = (i / (data.dd.length - 1)) * 900;
        const y = 222 + (Math.abs(d) / 4.0) * 34;
        return [x, y];
      });
      const ddLineD = ddPts.map((p, i) => `${i === 0 ? "M" : "L"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
      const ddAreaD = `M0,222 ${ddPts.map((p) => `L${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ")} L900,222 Z`;

      document.getElementById("equityLinePath")?.setAttribute("d", lineD);
      document.getElementById("equityAreaPath")?.setAttribute("d", areaD);
      const benchEl = document.getElementById("benchLinePath");
      if (benchEl) {
        benchEl.setAttribute("d", state.showBench ? benchD : "");
      }
      document.getElementById("drawdownLinePath")?.setAttribute("d", ddLineD);
      document.getElementById("drawdownAreaPath")?.setAttribute("d", ddAreaD);

      const lastPt = eqPts[eqPts.length - 1];
      const ptEl = document.getElementById("equityActivePoint");
      if (ptEl && lastPt) {
        ptEl.setAttribute("cx", lastPt[0].toFixed(1));
        ptEl.setAttribute("cy", lastPt[1].toFixed(1));
      }

      const axisEl = document.getElementById("equityAxis");
      if (axisEl) {
        axisEl.innerHTML = data.axis.map((a) => `<span>${a}</span>`).join("");
      }
    });
  }

  function renderOverviewScreener() {
    const tbody = document.getElementById("overviewScreenerBody");
    if (!tbody) return;
    const list = candidates
      .filter((c) => c.pool !== "F" && (state.overviewPool === "ALL" || c.pool === state.overviewPool))
      .slice(0, 6);

    tbody.innerHTML = list
      .map((c) => {
        const chgClass = c.change >= 0 ? "positive" : "negative";
        const chgSign = c.change >= 0 ? "+" : "";
        const trendClass = c.sma === "上涨" ? "up-text" : c.sma === "下跌" ? "down-text" : "neutral-text";
        const divClass = c.divergence.includes("底") ? "tag-up" : "tag-down";
        const scoreClass = c.score >= 80 ? "score-high" : "score-mid";
        return `<tr data-symbol="${c.symbol}">
          <td><b>${c.symbol}</b><span class="pair-suffix">-USDT</span></td>
          <td><span class="tag tag-neutral num">${c.pool}池</span></td>
          <td class="align-right num" data-cell-price="${c.symbol}">${formatPrice(c.price, c.decimals)}</td>
          <td class="align-right num ${chgClass}">${chgSign}${c.change.toFixed(2)}%</td>
          <td><span class="${trendClass}"><i class="status-dot ${c.sma === "上涨" ? "live" : c.sma === "下跌" ? "danger" : ""}"></i>${c.sma}</span></td>
          <td><span class="tag ${divClass}">${c.divergence}${c.divOrder === 2 ? " · 2次" : ""}</span></td>
          <td class="align-right">
            <div class="score-cell">
              <span class="${scoreClass} num">${c.score}</span>
              <span class="score-bar"><i style="width:${c.score}%"></i></span>
            </div>
          </td>
          <td class="align-right">${buildSparklineSvg(c.spark, c.change >= 0)}</td>
        </tr>`;
      })
      .join("");

    tbody.querySelectorAll("tr[data-symbol]").forEach((tr) => {
      tr.addEventListener("click", () => openSymbolInWorkspace(tr.dataset.symbol));
    });
  }

  function renderOverviewSignals() {
    const container = document.getElementById("overviewSignalList");
    if (!container) return;
    const sigs = candidates.filter((c) => c.pool !== "F").slice(0, 5);
    container.innerHTML = sigs
      .map((c) => {
        const isBull = c.divergence.includes("底");
        const tagCls = isBull ? "tag-up" : "tag-down";
        const actText = c.early ? "待收盘" : isBull ? "开多信号" : "开空信号";
        const actCls = c.early ? "warn-text" : isBull ? "up-text" : "down-text";
        return `<button class="signal-item" data-sig-symbol="${c.symbol}">
          <span class="signal-time num">${c.time}</span>
          <span class="signal-symbol">
            <b>${c.symbol}</b>
            <span class="tag ${tagCls}">${c.divergence} · ${c.pool}</span>
          </span>
          <strong class="num">${c.score}分</strong>
          <span class="signal-action ${actCls}">${actText}</span>
          <span class="chevron">→</span>
        </button>`;
      })
      .join("");

    container.querySelectorAll("[data-sig-symbol]").forEach((btn) => {
      btn.addEventListener("click", () => openSymbolInWorkspace(btn.dataset.sigSymbol));
    });
  }

  function renderOverviewPositions() {
    const tbody = document.getElementById("overviewPositionsBody");
    if (!tbody) return;
    if (positions.length === 0) {
      tbody.innerHTML = `<tr><td colspan="11" class="align-center muted-text" style="height:64px">暂无在途持仓 · 下次 4H 选币扫描将自动监控入场机会</td></tr>`;
      return;
    }
    tbody.innerHTML = positions
      .map((p) => {
        const isLong = p.side === "long";
        const pnlClass = p.pnl >= 0 ? "positive" : "negative";
        const pnlSign = p.pnl >= 0 ? "+" : "";
        const dec = p.symbol === "BTC" ? 1 : 2;
        return `<tr data-pos-row="${p.id}">
          <td><b>${p.symbol}</b><span class="pair-suffix">-USDT</span><span class="perp-badge">永续</span></td>
          <td><span class="tag ${isLong ? "tag-up" : "tag-down"}">${isLong ? "多" : "空"} · ${p.lever}</span></td>
          <td class="align-right num">${p.size}</td>
          <td class="align-right num">${formatPrice(p.entry, dec)}</td>
          <td class="align-right num" data-pos-mark="${p.symbol}">${formatPrice(p.mark, dec)}</td>
          <td class="align-right num muted-text">${formatPrice(p.liq, dec)}</td>
          <td class="align-right num ${pnlClass}" data-pos-pnl="${p.symbol}">${pnlSign}${p.pnl.toFixed(2)} (${pnlSign}${p.pnlPct.toFixed(2)}%)</td>
          <td class="align-right num"><span class="negative">${formatPrice(p.stop, dec)}</span> / <span class="positive">${formatPrice(p.tp, dec)}</span></td>
          <td><span>${p.strategy}</span> <span class="tag tag-neutral" style="margin-left:4px">${p.status}</span></td>
          <td class="align-right num muted-text">${p.duration}</td>
          <td class="align-right">
            <button class="table-action" data-close-pos="${p.id}">平仓</button>
            <button class="table-action" data-be-pos="${p.id}">移保本</button>
          </td>
        </tr>`;
      })
      .join("");

    tbody.querySelectorAll("[data-close-pos]").forEach((btn) => {
      btn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        promptClosePosition(btn.dataset.closePos);
      });
    });
    tbody.querySelectorAll("[data-be-pos]").forEach((btn) => {
      btn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        moveStopToBreakeven(btn.dataset.bePos);
      });
    });
  }

  // ==================== 5. SCREENER & SLIDE-OUT DETAIL DRAWER ====================
  function getFilteredScreenerList() {
    return candidates
      .filter((c) => {
        if (state.screenerPool !== "ALL" && c.pool !== state.screenerPool) return false;
        if (state.screenerSearch && !c.symbol.toLowerCase().includes(state.screenerSearch.toLowerCase())) return false;
        const primaryTrend = state.screenerMa === "SMA" ? c.sma : c.ema;
        if (!state.screenerTrends.has(primaryTrend)) return false;
        if (!state.screenerDivs.has(c.divergence)) return false;
        if (c.score < state.screenerMinScore) return false;
        if (state.screenerDiffOnly && c.sma === c.ema) return false;
        if (state.screenerOrder2Only && c.divOrder < 2) return false;
        if (!state.screenerEarlyOnly && c.early) return false;
        return true;
      })
      .sort((a, b) => {
        const key = state.screenerSortKey;
        const mult = state.screenerSortAsc ? 1 : -1;
        if (key === "symbol") return a.symbol.localeCompare(b.symbol) * mult;
        const valA = key === "volume" ? a.volNum : a[key];
        const valB = key === "volume" ? b.volNum : b[key];
        return (Number(valA) - Number(valB)) * mult;
      });
  }

  function renderScreenerTable() {
    safeExec(() => {
      const tbody = document.getElementById("screenerRows");
      if (!tbody) return;
      const list = getFilteredScreenerList();
      const matchEl = document.getElementById("filterMatchCount");
      if (matchEl) matchEl.textContent = `命中 ${list.length}`;

      if (list.length === 0) {
        tbody.innerHTML = `<tr><td colspan="15" class="align-center muted-text" style="height:80px">未找到符合当前筛选条件的交易对 · <button class="ghost-button" id="inlineResetFilter">点击重置筛选条件</button></td></tr>`;
        document.getElementById("inlineResetFilter")?.addEventListener("click", resetScreenerFilters);
        return;
      }

      tbody.innerHTML = list
        .map((c) => {
          const isSelected = c.symbol === state.selectedScreenerSymbol;
          const chgClass = c.change >= 0 ? "positive" : "negative";
          const chgSign = c.change >= 0 ? "+" : "";
          const distSign = c.dist >= 0 ? "+" : "";
          const slopeSign = c.slope >= 0 ? "+" : "";
          const smaCls = c.sma === "上涨" ? "up-text" : c.sma === "下跌" ? "down-text" : "neutral-text";
          const emaCls = c.ema === "上涨" ? "up-text" : c.ema === "下跌" ? "down-text" : "neutral-text";
          const hasDiff = c.sma !== c.ema;
          const divClass = c.divergence.includes("底") ? "tag-up" : c.divergence.includes("顶") ? "tag-down" : "tag-neutral";
          const scoreClass = c.score >= 80 ? "score-high" : c.score >= 65 ? "score-mid" : "score-low";

          return `<tr class="${isSelected ? "is-selected" : ""}" data-sc-symbol="${c.symbol}">
            <td>
              <b>${c.symbol}</b><span class="pair-suffix">-USDT</span>
              ${hasDiff ? `<span class="tag tag-warn" title="SMA200与EMA200趋势分歧" style="margin-left:4px">▲分歧</span>` : ""}
            </td>
            <td><span class="tag tag-neutral num">${c.pool}</span></td>
            <td class="align-right num" data-cell-price="${c.symbol}">${formatPrice(c.price, c.decimals)}</td>
            <td class="align-right num ${chgClass}">${chgSign}${c.change.toFixed(2)}%</td>
            <td class="align-right num">${c.volText}</td>
            <td><span class="${smaCls}"><i class="status-dot ${c.sma === "上涨" ? "live" : c.sma === "下跌" ? "danger" : ""}"></i>${c.sma}</span></td>
            <td><span class="${emaCls}"><i class="status-dot ${c.ema === "上涨" ? "live" : c.ema === "下跌" ? "danger" : ""}"></i>${c.ema}</span></td>
            <td class="align-right num ${c.dist >= 0 ? "positive" : "negative"}">${distSign}${c.dist.toFixed(1)}%</td>
            <td class="align-right num ${c.slope >= 0 ? "positive" : "negative"}">${slopeSign}${c.slope.toFixed(1)}%</td>
            <td>
              <span class="tag ${divClass}">${c.divergence}</span>
              ${c.divOrder === 2 ? `<span class="tag tag-up num" style="margin-left:3px">2次</span>` : ""}
              ${c.early ? `<span class="tag tag-warn" style="margin-left:3px">早期</span>` : ""}
            </td>
            <td class="align-center num ${c.heightOk ? "positive" : "muted-text"}">${c.heightOk ? "✓" : "·"}</td>
            <td class="align-center num ${c.areaOk ? "positive" : "muted-text"}">${c.areaOk ? "✓" : "·"}</td>
            <td class="align-center num ${c.volOk ? "positive" : "muted-text"}">${c.volOk ? "✓" : "·"}</td>
            <td class="align-right">
              <div class="score-cell">
                <span class="${scoreClass} num">${c.score}</span>
                <span class="score-bar"><i style="width:${c.score}%"></i></span>
              </div>
            </td>
            <td class="align-right num muted-text">${c.time}</td>
          </tr>`;
        })
        .join("");

      tbody.querySelectorAll("tr[data-sc-symbol]").forEach((tr) => {
        tr.addEventListener("click", () => {
          state.selectedScreenerSymbol = tr.dataset.scSymbol;
          renderScreenerTable();
          openScreenerDrawer(tr.dataset.scSymbol);
        });
        tr.addEventListener("dblclick", () => {
          openSymbolInWorkspace(tr.dataset.scSymbol);
        });
      });
    });
  }

  function openScreenerDrawer(symbol) {
    safeExec(() => {
      const c = candidates.find((item) => item.symbol === symbol);
      const drawer = document.getElementById("screenerDrawer");
      if (!c || !drawer) return;

      document.getElementById("drawerSymbol").textContent = `${c.symbol}-USDT 永续`;
      const poolTag = document.getElementById("drawerPoolTag");
      poolTag.textContent = `${c.pool}池 · ${c.divergence}`;
      poolTag.className = `tag ${c.divergence.includes("底") ? "tag-up" : c.divergence.includes("顶") ? "tag-down" : "tag-neutral"}`;

      const labels = [
        ["柱面积背离权重", c.scoreBreakdown[0], 25],
        ["柱峰值高度权重", c.scoreBreakdown[1], 20],
        ["价格极值突破度", c.scoreBreakdown[2], 15],
        ["G2 缩量衰竭配合", c.scoreBreakdown[3], 15],
        ["信号确认新鲜度", c.scoreBreakdown[4], 10],
        ["多周期/指标共振", c.scoreBreakdown[5], 15]
      ];

      const body = document.getElementById("drawerBodyContent");
      body.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:baseline;">
          <div>
            <span class="kpi-label">当前最新报价</span>
            <div class="price-lg num" style="padding:0">${formatPrice(c.price, c.decimals)} <small class="${c.change >= 0 ? "positive" : "negative"}">${c.change >= 0 ? "+" : ""}${c.change.toFixed(2)}%</small></div>
          </div>
          <div style="text-align:right">
            <span class="kpi-label">综合评分</span>
            <div class="score-big num">${c.score}</div>
          </div>
        </div>

        <div class="panel" style="padding:10px;background:var(--bg-canvas)">
          <div style="display:flex;justify-content:space-between;margin-bottom:6px">
            <span class="box-label">4H 价格与 MACD 柱段面积速览</span>
            <span class="num muted-text" style="font-size:11px">G1:${c.seg1Area} → G2:${c.seg2Area}</span>
          </div>
          ${buildSparklineSvg(c.spark, c.change >= 0).replace('class="sparkline-svg"', 'style="width:100%;height:56px"')}
        </div>

        <div class="panel" style="padding:12px">
          <span class="box-label">六维评分因子拆解 (§5.6.4)</span>
          <div style="display:grid;gap:8px;margin-top:10px">
            ${labels
              .map(
                ([name, val, max]) => `
              <div>
                <div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:3px">
                  <span>${name}</span>
                  <b class="num">${val} / ${max}</b>
                </div>
                <div class="meter"><span style="width:${((val / max) * 100).toFixed(0)}%;background:var(--up)"></span></div>
              </div>`
              )
              .join("")}
          </div>
        </div>

        <div class="briefing-box" style="margin:0">
          <span class="box-label">AI 二审摘要</span>
          <p>${c.aiBullets[0]}</p>
        </div>

        <button class="primary-button" id="drawerOpenWsBtn" style="width:100%;height:36px;margin-top:auto">
          在 K 线工作台打开 ${c.symbol}-USDT →
        </button>
      `;

      drawer.classList.add("is-open");
      drawer.setAttribute("aria-hidden", "false");
      document.getElementById("drawerOpenWsBtn")?.addEventListener("click", () => {
        openSymbolInWorkspace(c.symbol);
      });
    });
  }

  function closeScreenerDrawer() {
    const drawer = document.getElementById("screenerDrawer");
    if (drawer) {
      drawer.classList.remove("is-open");
      drawer.setAttribute("aria-hidden", "true");
    }
  }

  function resetScreenerFilters() {
    state.screenerPool = "ALL";
    state.screenerSearch = "";
    state.screenerTrends = new Set(["上涨", "下跌", "震荡"]);
    state.screenerDivs = new Set(["底背离", "顶背离", "无"]);
    state.screenerMinScore = 40;
    state.screenerDiffOnly = false;
    state.screenerOrder2Only = false;
    state.screenerEarlyOnly = true;

    const searchEl = document.getElementById("screenerSearchInput");
    if (searchEl) searchEl.value = "";
    const rangeEl = document.getElementById("scoreRangeInput");
    if (rangeEl) rangeEl.value = "40";
    const rangeLbl = document.getElementById("scoreRangeLabel");
    if (rangeLbl) rangeLbl.textContent = "≥ 40";

    document.querySelectorAll("#filterTrendGroup .filter-chip, #filterDivGroup .filter-chip").forEach((b) => b.classList.add("is-checked"));
    document.getElementById("filterDiffOnly")?.classList.remove("is-checked");
    document.getElementById("filterOrder2Only")?.classList.remove("is-checked");
    document.getElementById("filterEarlyOnly")?.classList.add("is-checked");
    renderScreenerTable();
    showToast("已重置全部选币筛选条件", "up");
  }

  // ==================== 6. WORKSPACE: TWO-STAGE K-LINE + DIVERGENCE ENGINE ====================
  let aiStageTimer = null;
  let chartStageTimers = [];

  function generateCandlesForCandidate(c) {
    const isBull = !c.divergence.includes("顶");
    const base = c.price;
    const step = base * 0.008;
    // Normalized 24 candles where index 6 is G1 extreme and index 16 is G2 extreme
    const offsets = isBull
      ? [2.2, 1.5, 0.8, -0.4, -1.6, -2.5, -3.2, -1.8, -0.5, 0.4, -0.2, -1.4, -2.6, -3.5, -4.1, -4.6, -5.0, -3.6, -2.1, -1.2, -0.4, 0.3, 0.8, 0.0]
      : [-2.2, -1.4, -0.6, 0.5, 1.8, 2.6, 3.3, 1.9, 0.6, -0.3, 0.4, 1.5, 2.7, 3.6, 4.2, 4.7, 5.1, 3.5, 2.0, 1.1, 0.3, -0.2, -0.6, 0.0];

    const histFactors = isBull
      ? [0.4, 0.2, -0.3, -0.8, -1.3, -1.7, -1.9, -1.2, -0.4, 0.3, 0.5, 0.2, -0.3, -0.6, -0.9, -1.05, -0.95, -0.5, -0.1, 0.35, 0.6, 0.75, 0.85, 0.9]
      : [-0.4, -0.2, 0.3, 0.8, 1.3, 1.7, 1.9, 1.2, 0.4, -0.3, -0.5, -0.2, 0.3, 0.6, 0.9, 1.05, 0.95, 0.5, 0.1, -0.35, -0.6, -0.75, -0.85, -0.9];

    return offsets.map((off, idx) => {
      const close = base + off * step;
      const open = base + (offsets[idx - 1] ?? off - 0.3) * step;
      const high = Math.max(open, close) + step * (0.45 + (idx % 3) * 0.2);
      const low = Math.min(open, close) - step * (0.45 + ((idx + 1) % 3) * 0.2);
      const sma = base + (isBull ? -3.8 + idx * 0.12 : 3.8 - idx * 0.12) * step;
      const ema = base + (isBull ? -3.1 + idx * 0.14 : 3.1 - idx * 0.14) * step;
      const vol = (idx >= 13 && idx <= 17 ? 0.58 : 0.95) * (0.7 + ((idx * 7) % 5) * 0.12);
      const hist = histFactors[idx];
      return { idx, open, high, low, close, sma, ema, vol, hist };
    });
  }

  function renderWorkspaceCandidates() {
    const container = document.getElementById("wsCandidateList");
    if (!container) return;
    const list = candidates.filter((c) => {
      if (state.wsPool !== "ALL" && c.pool !== state.wsPool) return false;
      if (state.wsSearch && !c.symbol.toLowerCase().includes(state.wsSearch.toLowerCase())) return false;
      return true;
    });

    container.innerHTML = list
      .map((c) => {
        const active = c.symbol === state.activeSymbol ? "is-active" : "";
        const chgCls = c.change >= 0 ? "positive" : "negative";
        const chgSign = c.change >= 0 ? "+" : "";
        const divCls = c.divergence.includes("底") ? "tag-up" : c.divergence.includes("顶") ? "tag-down" : "tag-neutral";
        return `<button class="candidate-item ${active}" data-ws-item="${c.symbol}">
          <div class="candidate-item-top">
            <b>${c.symbol}</b><small>-USDT</small>
          </div>
          <strong class="candidate-price num" data-cell-price="${c.symbol}">${formatPrice(c.price, c.decimals)}</strong>
          <div><span class="tag ${divCls}">${c.divergence} · ${c.score}</span></div>
          <span class="num ${chgCls}" style="justify-self:end;font-size:11px">${chgSign}${c.change.toFixed(2)}%</span>
        </button>`;
      })
      .join("");

    container.querySelectorAll("[data-ws-item]").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.activeSymbol = btn.dataset.wsItem;
        renderWorkspace();
      });
    });
  }

  function renderKlineChart(c) {
    const svg = document.getElementById("klineSvg");
    const chartWrap = document.getElementById("marketChart");
    if (!svg || !chartWrap || !c) return;

    // Clear previous stage timers
    chartStageTimers.forEach((t) => window.clearTimeout(t));
    chartStageTimers = [];
    chartWrap.classList.remove("stage-ma", "stage-bands", "stage-lines", "stage-labels");

    const bars = generateCandlesForCandidate(c);
    const isBull = !c.divergence.includes("顶");
    const allPrices = bars.flatMap((b) => [b.high, b.low, b.sma, b.ema]).concat([c.entry, c.stop, c.tp1]);
    const minP = Math.min(...allPrices);
    const maxP = Math.max(...allPrices);
    const spanP = maxP - minP || 1;

    const priceToY = (p) => 28 + (1 - (p - minP) / spanP) * 245;
    const idxToX = (i) => 42 + i * 34;

    // Grid lines & right price axis labels
    const gridLevels = [0.15, 0.38, 0.62, 0.85];
    const gridHtml = gridLevels
      .map((r) => {
        const y = (28 + r * 245).toFixed(1);
        const pVal = formatPrice(maxP - r * spanP, c.decimals);
        return `<line x1="0" y1="${y}" x2="850" y2="${y}" stroke="rgba(255,255,255,0.04)" />
                <text x="858" y="${Number(y) + 3}" fill="rgba(203,213,225,0.45)" font-family="var(--font-mono)" font-size="10">${pVal}</text>`;
      })
      .join("");

    // SMA200 & EMA200 paths
    const smaD = bars.map((b, i) => `${i === 0 ? "M" : "L"}${idxToX(i)},${priceToY(b.sma).toFixed(1)}`).join(" ");
    const emaD = bars.map((b, i) => `${i === 0 ? "M" : "L"}${idxToX(i)},${priceToY(b.ema).toFixed(1)}`).join(" ");

    // Candlesticks + Volume bars (y: 292..342) + MACD Histogram (zero axis at y=412)
    const candlesHtml = bars
      .map((b, i) => {
        const x = idxToX(i);
        const up = b.close >= b.open;
        const color = up ? "var(--up)" : "var(--down)";
        const yHigh = priceToY(b.high).toFixed(1);
        const yLow = priceToY(b.low).toFixed(1);
        const yTop = priceToY(Math.max(b.open, b.close)).toFixed(1);
        const yBot = priceToY(Math.min(b.open, b.close)).toFixed(1);
        const bodyH = Math.max(2, Number(yBot) - Number(yTop)).toFixed(1);

        // Volume
        const vH = (b.vol * 36).toFixed(1);
        const vY = (342 - Number(vH)).toFixed(1);

        // MACD Hist
        const inG1 = i >= 3 && i <= 8;
        const inG2 = i >= 12 && i <= 17;
        const mH = Math.max(2, Math.abs(b.hist) * 32).toFixed(1);
        const mY = b.hist >= 0 ? (412 - Number(mH)).toFixed(1) : "412";
        const mColor = b.hist >= 0 ? "var(--up)" : "var(--down)";
        const mOpacity = inG1 || inG2 ? "0.95" : "0.42";

        return `<g class="bar-group" data-bar-idx="${i}">
          <line x1="${x}" y1="${yHigh}" x2="${x}" y2="${yLow}" stroke="${color}" stroke-width="1.2" />
          <rect x="${x - 7}" y="${yTop}" width="14" height="${bodyH}" fill="${color}" rx="1" />
          <rect x="${x - 6}" y="${vY}" width="12" height="${vH}" fill="${color}" opacity="0.28" />
          <rect class="macd-bar" data-seg="${inG1 ? "G1" : inG2 ? "G2" : ""}" x="${x - 7}" y="${mY}" width="14" height="${mH}" fill="${mColor}" opacity="${mOpacity}" rx="1" />
        </g>`;
      })
      .join("");

    // Divergence Highlight Bands & Lines (§17.9)
    const xG1 = idxToX(6);
    const xG2 = idxToX(16);
    const yPriceG1 = priceToY(isBull ? bars[6].low : bars[6].high).toFixed(1);
    const yPriceG2 = priceToY(isBull ? bars[16].low : bars[16].high).toFixed(1);
    const yMacdG1 = (412 - bars[6].hist * 32).toFixed(1);
    const yMacdG2 = (412 - bars[15].hist * 32).toFixed(1);
    const divColor = isBull ? "var(--up)" : "var(--down)";
    const bandFill = isBull ? "rgba(0, 245, 160, 0.07)" : "rgba(255, 77, 94, 0.07)";

    // Entry / Stop / TP horizontal lines
    const yEntry = priceToY(c.entry).toFixed(1);
    const yStop = priceToY(c.stop).toFixed(1);
    const yTp = priceToY(c.tp1).toFixed(1);

    const planHtml = state.showPlan
      ? `<g class="plan-lines-group">
          <line class="entry-line" x1="0" y1="${yEntry}" x2="848" y2="${yEntry}" />
          <rect x="766" y="${Number(yEntry) - 9}" width="82" height="16" rx="2" fill="#181B1F" stroke="rgba(255,255,255,0.2)" />
          <text x="772" y="${Number(yEntry) + 3}" class="chart-label">入场 ${formatPrice(c.entry, c.decimals)}</text>

          <line class="stop-line" x1="0" y1="${yStop}" x2="848" y2="${yStop}" />
          <rect x="766" y="${Number(yStop) - 9}" width="82" height="16" rx="2" fill="rgba(255,77,94,0.14)" stroke="var(--down-line)" />
          <text x="772" y="${Number(yStop) + 3}" class="chart-label stop-label">止损 ${formatPrice(c.stop, c.decimals)}</text>

          <line class="take-line" x1="0" y1="${yTp}" x2="848" y2="${yTp}" />
          <rect x="766" y="${Number(yTp) - 9}" width="82" height="16" rx="2" fill="rgba(0,245,160,0.14)" stroke="var(--up-line)" />
          <text x="772" y="${Number(yTp) + 3}" class="chart-label take-label">止盈 ${formatPrice(c.tp1, c.decimals)}</text>
        </g>`
      : "";

    const areaReduction = Math.round((1 - c.seg2Area / (c.seg1Area || 1)) * 100);

    svg.innerHTML = `
      ${gridHtml}
      <line x1="0" y1="286" x2="920" y2="286" stroke="rgba(255,255,255,0.07)" />
      <line x1="0" y1="348" x2="920" y2="348" stroke="rgba(255,255,255,0.07)" />
      <line x1="0" y1="412" x2="850" y2="412" stroke="rgba(255,255,255,0.12)" stroke-dasharray="2 4" />
      <text x="12" y="304" fill="rgba(203,213,225,0.45)" font-family="var(--font-mono)" font-size="10">VOL · 缩量配合 ${c.volRatio}×</text>
      <text x="12" y="366" fill="rgba(203,213,225,0.45)" font-family="var(--font-mono)" font-size="10">MACD(12,26,9) · 柱段面积对比</text>

      <!-- Stage 300ms: MACD Segment Highlight Bands -->
      <g class="div-band">
        <rect x="${idxToX(3) - 12}" y="354" width="${idxToX(8) - idxToX(3) + 24}" height="106" fill="${bandFill}" rx="3" />
        <rect x="${idxToX(12) - 12}" y="354" width="${idxToX(17) - idxToX(12) + 24}" height="106" fill="${bandFill}" rx="3" />
      </g>

      <!-- Stage 120ms: Moving Averages -->
      ${state.showSma ? `<path class="ma-line" d="${smaD}" />` : ""}
      ${state.showEma ? `<path class="ema-line" d="${emaD}" />` : ""}

      <!-- Stage 0ms: Candlesticks, Volume & MACD Histogram -->
      ${candlesHtml}
      ${planHtml}

      <!-- Stage 400ms: Price & Indicator Divergence Lines -->
      <g class="div-lines-interactive" style="color:${divColor}">
        <line class="divergence-draw-line" x1="${xG1}" y1="${yPriceG1}" x2="${xG2}" y2="${yPriceG2}" stroke="${divColor}" stroke-width="1.8" />
        <circle cx="${xG1}" cy="${yPriceG1}" r="8" fill="${divColor}" opacity="0.14" />
        <circle cx="${xG1}" cy="${yPriceG1}" r="3.5" fill="${divColor}" />
        <circle cx="${xG2}" cy="${yPriceG2}" r="8" fill="${divColor}" opacity="0.14" />
        <circle cx="${xG2}" cy="${yPriceG2}" r="3.5" fill="${divColor}" />

        <line class="divergence-draw-line" x1="${xG1}" y1="${yMacdG1}" x2="${idxToX(15)}" y2="${yMacdG2}" stroke="${divColor}" stroke-width="1.4" stroke-dasharray="4 3" />
        <circle cx="${xG1}" cy="${yMacdG1}" r="3" fill="${divColor}" />
        <circle cx="${idxToX(15)}" cy="${yMacdG2}" r="3" fill="${divColor}" />
      </g>

      <!-- Stage 1000ms: Area Badges & Signal Triangle -->
      <g class="div-meta-group">
        <rect x="${xG1 - 36}" y="463" width="76" height="18" rx="3" fill="#111316" stroke="rgba(255,255,255,0.12)" />
        <text x="${xG1 - 28}" y="475" fill="#CBD5E1" font-family="var(--font-mono)" font-size="10">G1面积 ${c.seg1Area}</text>

        <rect x="${xG2 - 52}" y="463" width="118" height="18" rx="3" fill="#111316" stroke="${divColor}" stroke-opacity="0.4" />
        <text x="${xG2 - 44}" y="475" fill="${divColor}" font-family="var(--font-mono)" font-size="10">G2面积 ${c.seg2Area} (-${areaReduction}%)</text>

        <polygon points="${idxToX(18)},${Number(yPriceG2) + (isBull ? 10 : -10)} ${idxToX(18) - 6},${Number(yPriceG2) + (isBull ? 20 : -20)} ${idxToX(18) + 6},${Number(yPriceG2) + (isBull ? 20 : -20)}" fill="${divColor}" />
        <text x="${idxToX(18) + 10}" y="${Number(yPriceG2) + (isBull ? 19 : -13)}" fill="${divColor}" font-family="var(--font-mono)" font-weight="600" font-size="11">${c.divergence} · ${c.score}分</text>
      </g>

      <!-- Interactive Crosshair Overlay -->
      <g id="chartCrosshair" style="display:none;pointer-events:none">
        <line id="crossV" x1="0" y1="0" x2="0" y2="490" stroke="rgba(255,255,255,0.28)" stroke-dasharray="3 3" />
        <line id="crossH" x1="0" y1="0" x2="850" y2="0" stroke="rgba(255,255,255,0.28)" stroke-dasharray="3 3" />
        <rect id="crossPriceBox" x="850" y="0" width="68" height="18" rx="2" fill="#181B1F" stroke="rgba(255,255,255,0.25)" />
        <text id="crossPriceText" x="855" y="12" fill="#FFFFFF" font-family="var(--font-mono)" font-size="10"></text>
      </g>
    `;

    // Choreographed Entrance Sequence (§17.9: 0ms K-line -> 120ms MA -> 300ms Bands -> 400ms Lines -> 1000ms Labels)
    chartStageTimers.push(window.setTimeout(() => chartWrap.classList.add("stage-ma"), 90));
    chartStageTimers.push(window.setTimeout(() => chartWrap.classList.add("stage-bands"), 220));
    chartStageTimers.push(window.setTimeout(() => chartWrap.classList.add("stage-lines"), 320));
    chartStageTimers.push(window.setTimeout(() => chartWrap.classList.add("stage-labels"), 680));

    // Bind Crosshair & Interactive Sample Annotation
    bindChartInteractions(svg, bars, c, minP, maxP, spanP);
  }

  function bindChartInteractions(svg, bars, c, minP, maxP, spanP) {
    const crossG = svg.querySelector("#chartCrosshair");
    const crossV = svg.querySelector("#crossV");
    const crossH = svg.querySelector("#crossH");
    const crossBox = svg.querySelector("#crossPriceBox");
    const crossTxt = svg.querySelector("#crossPriceText");

    svg.onpointermove = (ev) => {
      const rect = svg.getBoundingClientRect();
      const relX = ((ev.clientX - rect.left) / rect.width) * 920;
      const relY = ((ev.clientY - rect.top) / rect.height) * 490;
      if (relX > 850) {
        if (crossG) crossG.style.display = "none";
        return;
      }
      if (crossG) crossG.style.display = "block";
      const barIdx = Math.max(0, Math.min(bars.length - 1, Math.round((relX - 42) / 34)));
      const snapX = 42 + barIdx * 34;
      crossV?.setAttribute("x1", snapX);
      crossV?.setAttribute("x2", snapX);
      crossH?.setAttribute("y1", relY.toFixed(1));
      crossH?.setAttribute("y2", relY.toFixed(1));

      if (relY >= 24 && relY <= 280) {
        const hoveredPrice = maxP - ((relY - 28) / 245) * spanP;
        crossBox?.setAttribute("y", (relY - 9).toFixed(1));
        if (crossTxt) {
          crossTxt.setAttribute("y", (relY + 3).toFixed(1));
          crossTxt.textContent = formatPrice(hoveredPrice, c.decimals);
        }
      }

      const b = bars[barIdx];
      if (b) {
        document.getElementById("legendO").textContent = formatPrice(b.open, c.decimals);
        document.getElementById("legendH").textContent = formatPrice(b.high, c.decimals);
        document.getElementById("legendL").textContent = formatPrice(b.low, c.decimals);
        const legC = document.getElementById("legendC");
        if (legC) {
          legC.textContent = formatPrice(b.close, c.decimals);
          legC.className = `num ${b.close >= b.open ? "positive" : "negative"}`;
        }
        document.getElementById("legendSma").textContent = formatPrice(b.sma, c.decimals);
        document.getElementById("legendEma").textContent = formatPrice(b.ema, c.decimals);
        const legHist = document.getElementById("legendHist");
        if (legHist) {
          legHist.textContent = (b.hist >= 0 ? "+" : "") + b.hist.toFixed(2);
          legHist.className = `num ${b.hist >= 0 ? "positive" : "negative"}`;
        }
      }
    };

    svg.onpointerleave = () => {
      if (crossG) crossG.style.display = "none";
    };

    // Interactive Golden Sample Point-and-Click (§5.6.6 & §17.6.3)
    svg.onclick = (ev) => {
      if (!state.annotateMode) return;
      const rect = svg.getBoundingClientRect();
      const relX = ((ev.clientX - rect.left) / rect.width) * 920;
      const barIdx = Math.max(0, Math.min(bars.length - 1, Math.round((relX - 42) / 34)));
      state.annotateClicks.push(barIdx);
      const stepEl = document.getElementById("annotationStepText");
      if (state.annotateClicks.length === 1) {
        if (stepEl) stepEl.textContent = `已记录 G1 (K线 #${barIdx}) · 请继续点击第 2 段柱子 (G2) (1/2)`;
        showToast(`已选中第 1 段 MACD 柱段 (G1)，请继续点击第 2 段 (G2)`, "warn");
      } else if (state.annotateClicks.length >= 2) {
        const [g1Idx, g2Idx] = state.annotateClicks;
        state.annotateClicks = [];
        state.annotateMode = false;
        document.getElementById("annotationBanner")?.classList.remove("is-visible");
        document.getElementById("annotateModeBtn")?.classList.remove("primary-button");
        promptSaveGoldenSample(c, g1Idx, g2Idx);
      }
    };
  }

  function promptSaveGoldenSample(c, g1Idx, g2Idx) {
    const isBull = !c.divergence.includes("顶");
    openActionDialog({
      title: `保存金标背离样本 · ${c.symbol}-USDT (${state.timeframe})`,
      bodyHtml: `
        <div class="detail-row"><span>识别背离类型</span><strong class="num">${isBull ? "BULL_REGULAR (常规底背离)" : "BEAR_REGULAR (常规顶背离)"}</strong></div>
        <div class="detail-row"><span>选定柱段区间</span><strong class="num">G1 (Bar #${g1Idx}) → G2 (Bar #${g2Idx})</strong></div>
        <div class="detail-row"><span>柱面积缩减比</span><strong class="num positive">${c.seg1Area} → ${c.seg2Area} (符合阈值)</strong></div>
        <div class="form-field" style="margin-top:8px">
          <label>金标样本备注 (同步写入 tests/fixtures/golden_divergences.csv)</label>
          <input class="form-input" id="goldenNoteInput" value="两段柱子面积明显缩小，缩量 ${c.volRatio}x 配合良好" />
        </div>
      `,
      footerHtml: `
        <button class="secondary-button" id="cancelGoldenBtn">取消</button>
        <button class="primary-button" id="confirmGoldenBtn">保存入库并导出 CSV</button>
      `,
      onMount: (dlg) => {
        dlg.querySelector("#cancelGoldenBtn")?.addEventListener("click", closeActionDialog);
        dlg.querySelector("#confirmGoldenBtn")?.addEventListener("click", () => {
          const note = dlg.querySelector("#goldenNoteInput")?.value || "人工网页标注样本";
          goldenSamples.unshift({
            id: `GS-0${goldenSamples.length + 1}`,
            symbol: `${c.symbol}-USDT-SWAP`,
            bar: state.timeframe,
            kind: isBull ? "BULL_REGULAR" : "BEAR_REGULAR",
            g1: "2026-10-22 08:00",
            g2: "2026-10-24 16:00",
            source: "web_ui",
            note
          });
          document.getElementById("goldenCountBadge").textContent = String(goldenSamples.length);
          state.activeDockTab = "golden";
          renderWorkspaceDock();
          closeActionDialog();
          showToast(`已将 ${c.symbol}-USDT 金标样本写入数据库与 golden_divergences.csv`, "up");
        });
      }
    });
  }

  function renderWorkspaceDetail(c) {
    if (!c) return;
    const isBull = !c.divergence.includes("顶");
    document.getElementById("wsSymbolTitle").textContent = `${c.symbol}-USDT`;
    const priceEl = document.getElementById("wsLivePrice");
    if (priceEl) priceEl.textContent = formatPrice(c.price, c.decimals);

    const chgEl = document.getElementById("wsLiveChange");
    if (chgEl) {
      chgEl.textContent = `${c.change >= 0 ? "+" : ""}${c.change.toFixed(2)}%`;
      chgEl.className = `tag ${c.change >= 0 ? "tag-up" : "tag-down"} num`;
    }
    document.getElementById("wsHigh").textContent = formatPrice(c.high24, c.decimals);
    document.getElementById("wsLow").textContent = formatPrice(c.low24, c.decimals);
    document.getElementById("wsVol").textContent = c.volText;
    document.getElementById("wsFunding").textContent = c.funding;

    // Right sidebar Stage 1: Instant Math Metrics
    const dTag = document.getElementById("detailTag");
    if (dTag) {
      dTag.textContent = `${c.divergence} · ${c.pool}池`;
      dTag.className = `tag ${isBull ? "tag-up" : "tag-down"}`;
    }
    const oTag = document.getElementById("detailOrderTag");
    if (oTag) {
      oTag.textContent = c.divOrder === 2 ? "二次背离 +15" : "一次标准背离";
    }
    document.getElementById("detailScore").textContent = String(c.score);
    document.getElementById("metricPrice").innerHTML = `${formatPrice(c.seg2Price, c.decimals)} ${isBull ? "&lt;" : "&gt;"} ${formatPrice(c.seg1Price, c.decimals)} <b class="positive">✓</b>`;
    document.getElementById("metricHeight").innerHTML = `${c.seg1Peak} → ${c.seg2Peak} <b class="${c.heightOk ? "positive" : "muted-text"}">${c.heightOk ? "✓" : "·"}</b>`;
    const areaPct = Math.round((1 - c.seg2Area / (c.seg1Area || 1)) * 100);
    document.getElementById("metricArea").innerHTML = `${c.seg1Area} → ${c.seg2Area} (-${areaPct}%) <b class="${c.areaOk ? "positive" : "muted-text"}">${c.areaOk ? "✓" : "·"}</b>`;
    document.getElementById("metricVol").innerHTML = `缩量 ${c.volRatio}× <b class="${c.volOk ? "positive" : "warn-text"}">${c.volOk ? "✓" : "弱"}</b>`;
    document.getElementById("metricTime").textContent = `10-24 ${c.time} (UTC+8)`;

    // Stage 2: AI Progressive Rendering (Skeleton shimmer -> 220ms fade in)
    const skel = document.getElementById("aiSkeleton");
    const aiCont = document.getElementById("aiContent");
    if (skel && aiCont) {
      skel.style.display = "block";
      aiCont.style.opacity = "0";
      window.clearTimeout(aiStageTimer);
      aiStageTimer = window.setTimeout(() => {
        skel.style.display = "none";
        document.getElementById("aiHeadline").textContent = c.aiStatus;
        document.getElementById("aiScoreMod").textContent = `${c.aiMod} 修正分`;
        document.getElementById("aiBullets").innerHTML = c.aiBullets.map((b) => `<li>${b}</li>`).join("");
        aiCont.style.transition = "opacity 180ms ease";
        aiCont.style.opacity = "1";
      }, 220);
    }

    // Strategy Box & Trade Button
    document.getElementById("planEntry").textContent = `${isBull ? "≥" : "≤"} ${formatPrice(c.entry, c.decimals)} (1H突破)`;
    const stopPct = (((c.stop - c.entry) / c.entry) * 100).toFixed(2);
    document.getElementById("planStop").innerHTML = `${formatPrice(c.stop, c.decimals)} <em class="negative">(${stopPct}%)</em>`;
    document.getElementById("planTp").textContent = `1.5R (${formatPrice(c.tp1, c.decimals)}) / 2.0R (${formatPrice(c.tp2, c.decimals)})`;
    document.getElementById("planSize").textContent = `${c.size} 张 (逐仓 3x)`;

    const tradeBtn = document.getElementById("wsTradeBtn");
    const tradeTxt = document.getElementById("wsTradeBtnText");
    const tradeModeLbl = document.getElementById("wsTradeModeLabel");
    if (tradeBtn && tradeTxt && tradeModeLbl) {
      tradeBtn.className = `trade-button ${isBull ? "up-button" : "down-button"}`;
      tradeTxt.textContent = `${isBull ? "开多" : "开空"} ${c.symbol}-USDT`;
      tradeModeLbl.textContent = state.mode === "live" ? "· 实盘需二次确认" : "· 模拟盘全自动";
    }
  }

  function renderWorkspaceDock() {
    const head = document.getElementById("wsDockHead");
    const body = document.getElementById("wsDockBody");
    if (!head || !body) return;

    document.querySelectorAll("#wsDockTabs .pool-tab").forEach((b) => {
      b.classList.toggle("is-active", b.dataset.dock === state.activeDockTab);
    });

    if (state.activeDockTab === "positions") {
      head.innerHTML = `<tr><th>交易对</th><th>方向</th><th class="align-right">张数</th><th class="align-right">开仓均价</th><th class="align-right">标记价</th><th class="align-right">未实现盈亏</th><th class="align-right">止损 / 止盈</th><th>策略</th></tr>`;
      body.innerHTML = positions
        .map(
          (p) => `<tr>
          <td><b>${p.symbol}</b><span class="pair-suffix">-USDT</span></td>
          <td><span class="tag ${p.side === "long" ? "tag-up" : "tag-down"}">${p.side === "long" ? "多" : "空"}</span></td>
          <td class="align-right num">${p.size}</td>
          <td class="align-right num">${p.entry}</td>
          <td class="align-right num">${p.mark}</td>
          <td class="align-right num ${p.pnl >= 0 ? "positive" : "negative"}">${p.pnl >= 0 ? "+" : ""}${p.pnl.toFixed(2)} USDT</td>
          <td class="align-right num">${p.stop} / ${p.tp}</td>
          <td>${p.strategy}</td>
        </tr>`
        )
        .join("");
    } else if (state.activeDockTab === "orders") {
      head.innerHTML = `<tr><th>客户端订单号 (clOrdId)</th><th>币种</th><th>方向</th><th>类型</th><th class="align-right">委托价</th><th class="align-right">张数</th><th>保护止损</th><th>状态</th></tr>`;
      body.innerHTML = openOrders
        .map(
          (o) => `<tr>
          <td class="num muted-text">${o.clOrdId}</td>
          <td><b>${o.symbol}</b>-USDT</td>
          <td><span class="tag ${o.side === "long" ? "tag-up" : "tag-down"}">${o.side === "long" ? "开多" : "开空"}</span></td>
          <td>${o.type}</td>
          <td class="align-right num">${o.price}</td>
          <td class="align-right num">${o.size}</td>
          <td class="num negative">${o.stop}</td>
          <td><span class="tag tag-up">${o.status}</span></td>
        </tr>`
        )
        .join("");
    } else if (state.activeDockTab === "history") {
      head.innerHTML = `<tr><th>确认时间</th><th>币种</th><th>周期</th><th>背离类型</th><th class="align-right">评分</th><th>AI 二审结论</th><th>执行结果</th></tr>`;
      body.innerHTML = `
        <tr><td class="num">10-24 16:00</td><td><b>${state.activeSymbol}</b>-USDT</td><td class="num">4H</td><td><span class="tag tag-up">底背离 · 2次</span></td><td class="align-right num">82</td><td>+8分 · 形态干净</td><td class="positive">已入场持仓中</td></tr>
        <tr><td class="num">10-19 08:00</td><td><b>${state.activeSymbol}</b>-USDT</td><td class="num">4H</td><td><span class="tag tag-up">底背离 · 1次</span></td><td class="align-right num">77</td><td>+5分 · 缩量回踩</td><td class="positive">止盈 +1.8R 平仓</td></tr>
        <tr><td class="num">10-11 20:00</td><td><b>${state.activeSymbol}</b>-USDT</td><td class="num">4H</td><td><span class="tag tag-down">顶背离 · 1次</span></td><td class="align-right num">71</td><td>-2分 · 逆势放弃</td><td class="muted-text">风控过滤未执行</td></tr>
      `;
    } else if (state.activeDockTab === "golden") {
      head.innerHTML = `<tr><th>样本编号</th><th>合约</th><th>周期</th><th>背离类型</th><th>G1 时间</th><th>G2 时间</th><th>人工备注</th></tr>`;
      body.innerHTML = goldenSamples
        .map(
          (g) => `<tr>
          <td class="num">${g.id}</td>
          <td><b>${g.symbol}</b></td>
          <td class="num">${g.bar}</td>
          <td><span class="tag ${g.kind.includes("BULL") ? "tag-up" : "tag-down"}">${g.kind}</span></td>
          <td class="num muted-text">${g.g1}</td>
          <td class="num muted-text">${g.g2}</td>
          <td>${g.note}</td>
        </tr>`
        )
        .join("");
    }
  }

  function renderWorkspace() {
    safeExec(() => {
      const c = candidates.find((item) => item.symbol === state.activeSymbol) || candidates[0];
      renderWorkspaceCandidates();
      renderKlineChart(c);
      renderWorkspaceDetail(c);
      renderWorkspaceDock();
    });
  }

  // ==================== 7. POSITIONS, ORDERS & HOLD-TO-CONFIRM ENGINE ====================
  function renderConfirmQueue() {
    const strip = document.getElementById("confirmQueueStrip");
    if (!strip) return;
    document.getElementById("navConfirmCount").textContent = String(pendingConfirms.length);

    if (pendingConfirms.length === 0) {
      strip.innerHTML = "";
      return;
    }

    strip.innerHTML = pendingConfirms
      .map((item) => {
        const mins = Math.floor(item.remainSec / 60);
        const secs = String(item.remainSec % 60).padStart(2, "0");
        const pctRing = Math.max(0, Math.min(100, (item.remainSec / 900) * 100));
        const dashOffset = (100 - pctRing).toFixed(1);
        const ringColor = item.remainSec > 300 ? "var(--text-primary)" : item.remainSec > 60 ? "var(--warn)" : "var(--down)";
        const slipRatio = Math.min(100, (item.slipPct / item.maxSlip) * 100);

        return `<div class="panel confirm-card spotlight-surface" data-cfm-card="${item.id}">
          <div class="countdown-ring" title="15分钟一次性确认令牌有效期">
            <svg viewBox="0 0 36 36">
              <circle cx="18" cy="18" r="15.9" fill="none" stroke="var(--bg-surface-3)" stroke-width="2.2" />
              <circle cx="18" cy="18" r="15.9" fill="none" stroke="${ringColor}" stroke-width="2.2" stroke-dasharray="100" stroke-dashoffset="${dashOffset}" />
            </svg>
            <span class="num">${mins}:${secs}</span>
          </div>
          <div style="display:grid;gap:4px">
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
              <b style="color:var(--text-primary);font-size:14px">${item.symbol}-USDT 永续</b>
              <span class="tag tag-up">${item.side} · ${item.pool} · ${item.score}分</span>
              <span class="tag tag-warn">待确认令牌</span>
            </div>
            <div class="num muted-text" style="font-size:11px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">
              <span>信号价 <b>${item.signalPrice}</b></span>
              <span>现价 <b>${item.currentPrice}</b></span>
              <span>偏离 <b class="positive">+${item.slipPct.toFixed(2)}%</b> / 上限 ${item.maxSlip}%</span>
              <span class="meter" style="width:68px;display:inline-block"><span style="width:${slipRatio}%;background:var(--up)"></span></span>
            </div>
            <div class="num muted-text" style="font-size:11px">
              止损 <b class="negative">${item.stop}</b> · 止盈 <b class="positive">${item.tp}</b> · 仓位 <b>${item.size} (风险 ${item.risk})</b>
            </div>
          </div>
          <div style="display:flex;gap:6px;align-items:center">
            <button class="secondary-button" data-dismiss-cfm="${item.id}">放弃</button>
            <button class="primary-button hold-button" data-hold-cfm="${item.id}" title="按住 1 秒防误触确认下单">
              <span class="hold-progress"></span>
              <span style="position:relative;z-index:1">长按1秒确认下单</span>
            </button>
          </div>
        </div>`;
      })
      .join("");

    strip.querySelectorAll("[data-dismiss-cfm]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = pendingConfirms.findIndex((x) => x.id === btn.dataset.dismissCfm);
        if (idx >= 0) {
          const removed = pendingConfirms.splice(idx, 1)[0];
          renderConfirmQueue();
          showToast(`已放弃 ${removed.symbol}-USDT 待确认信号`, "warn");
        }
      });
    });

    strip.querySelectorAll("[data-hold-cfm]").forEach((btn) => {
      bindHoldToConfirm(btn, 900, () => {
        const idx = pendingConfirms.findIndex((x) => x.id === btn.dataset.holdCfm);
        if (idx >= 0) {
          const cfm = pendingConfirms.splice(idx, 1)[0];
          positions.unshift({
            id: `pos-${cfm.symbol.toLowerCase()}-${Date.now()}`,
            symbol: cfm.symbol,
            side: "long",
            lever: "3x 逐仓",
            size: cfm.size.replace(" 张", ""),
            entry: cfm.currentPrice,
            mark: cfm.currentPrice,
            liq: Number((cfm.currentPrice * 0.62).toFixed(4)),
            pnl: 0.0,
            pnlPct: 0.0,
            stop: cfm.stop,
            tp: Number((cfm.currentPrice * 1.08).toFixed(4)),
            strategy: "S1 确认执行",
            duration: "00:00:02",
            status: "已挂reduceOnly止损"
          });
          renderConfirmQueue();
          renderPortfolioTable();
          renderOverviewPositions();
          document.getElementById("ptabPosCount").textContent = String(positions.length);
          showToast(`已确认下单 ${cfm.symbol}-USDT ${cfm.size} @ ${cfm.currentPrice} (附带交易所端止损)`, "up");
        }
      });
    });
  }

  function bindHoldToConfirm(buttonEl, durationMs, onComplete) {
    if (!buttonEl) return;
    const bar = buttonEl.querySelector(".hold-progress");
    let startTs = 0;
    let rafId = null;

    const cancelHold = () => {
      if (rafId) cancelAnimationFrame(rafId);
      rafId = null;
      if (bar) bar.style.width = "0%";
    };

    const step = (now) => {
      const elapsed = now - startTs;
      const pct = Math.min(100, (elapsed / durationMs) * 100);
      if (bar) bar.style.width = `${pct.toFixed(1)}%`;
      if (pct >= 100) {
        cancelHold();
        onComplete();
      } else {
        rafId = requestAnimationFrame(step);
      }
    };

    buttonEl.addEventListener("pointerdown", (ev) => {
      if (ev.button !== 0) return;
      startTs = performance.now();
      rafId = requestAnimationFrame(step);
    });
    buttonEl.addEventListener("pointerup", cancelHold);
    buttonEl.addEventListener("pointerleave", cancelHold);
    buttonEl.addEventListener("pointercancel", cancelHold);
  }

  function renderPortfolioTable() {
    const head = document.getElementById("portfolioTableHead");
    const body = document.getElementById("portfolioTableBody");
    if (!head || !body) return;

    document.querySelectorAll("#portfolioTabs button").forEach((b) => {
      b.classList.toggle("is-active", b.dataset.ptab === state.activePortfolioTab);
    });

    if (state.activePortfolioTab === "positions") {
      head.innerHTML = `<tr><th>币种</th><th>方向</th><th class="align-right">数量(张)</th><th class="align-right">开仓均价</th><th class="align-right">标记价</th><th class="align-right">强平价</th><th class="align-right">未实现盈亏</th><th class="align-right">保护止损 / 止盈</th><th>策略与状态</th><th class="align-right">操作</th></tr>`;
      body.innerHTML = positions
        .map((p) => {
          const isLong = p.side === "long";
          const pnlCls = p.pnl >= 0 ? "positive" : "negative";
          const sign = p.pnl >= 0 ? "+" : "";
          return `<tr>
            <td><b>${p.symbol}</b><span class="pair-suffix">-USDT 永续</span></td>
            <td><span class="tag ${isLong ? "tag-up" : "tag-down"}">${isLong ? "多" : "空"} · ${p.lever}</span></td>
            <td class="align-right num">${p.size}</td>
            <td class="align-right num">${p.entry}</td>
            <td class="align-right num" data-pos-mark="${p.symbol}">${p.mark}</td>
            <td class="align-right num muted-text">${p.liq}</td>
            <td class="align-right num ${pnlCls}" data-pos-pnl="${p.symbol}">${sign}${p.pnl.toFixed(2)} USDT (${sign}${p.pnlPct.toFixed(2)}%)</td>
            <td class="align-right num"><span class="negative">${p.stop}</span> / <span class="positive">${p.tp}</span></td>
            <td>${p.strategy} <span class="tag tag-neutral">${p.status}</span></td>
            <td class="align-right">
              <button class="table-action" data-close-pos="${p.id}">平仓</button>
              <button class="table-action" data-be-pos="${p.id}">移保本</button>
            </td>
          </tr>`;
        })
        .join("");

      body.querySelectorAll("[data-close-pos]").forEach((btn) =>
        btn.addEventListener("click", () => promptClosePosition(btn.dataset.closePos))
      );
      body.querySelectorAll("[data-be-pos]").forEach((btn) =>
        btn.addEventListener("click", () => moveStopToBreakeven(btn.dataset.bePos))
      );
    } else if (state.activePortfolioTab === "openOrders") {
      head.innerHTML = `<tr><th>客户端订单号</th><th>币种</th><th>方向</th><th>订单类型</th><th class="align-right">委托价</th><th class="align-right">委托张数</th><th>附带止损</th><th>状态</th><th class="align-right">操作</th></tr>`;
      body.innerHTML = openOrders
        .map(
          (o, idx) => `<tr>
          <td class="num muted-text">${o.clOrdId}</td>
          <td><b>${o.symbol}</b>-USDT 永续</td>
          <td><span class="tag ${o.side === "long" ? "tag-up" : "tag-down"}">${o.side === "long" ? "开多" : "开空"}</span></td>
          <td>${o.type}</td>
          <td class="align-right num">${o.price}</td>
          <td class="align-right num">${o.size}</td>
          <td class="num negative">${o.stop}</td>
          <td><span class="tag tag-up">${o.status}</span></td>
          <td class="align-right"><button class="table-action" data-cancel-ord="${idx}">撤单</button></td>
        </tr>`
        )
        .join("");
      body.querySelectorAll("[data-cancel-ord]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const i = Number(btn.dataset.cancelOrd);
          const removed = openOrders.splice(i, 1)[0];
          renderPortfolioTable();
          if (removed) showToast(`已撤销委托单 ${removed.clOrdId}`, "warn");
        });
      });
    } else if (state.activePortfolioTab === "algoOrders") {
      head.innerHTML = `<tr><th>保护单号</th><th>币种</th><th>动作</th><th>止损触发条件 (Mark)</th><th>止盈触发条件 (Mark)</th><th class="align-right">数量</th><th>只减仓 (reduceOnly)</th><th>状态</th></tr>`;
      body.innerHTML = algoOrders
        .map(
          (a) => `<tr>
          <td class="num">${a.id}</td>
          <td><b>${a.symbol}</b>-USDT</td>
          <td>${a.side}</td>
          <td class="num negative">${a.trigger}</td>
          <td class="num positive">${a.tpTrigger}</td>
          <td class="align-right num">${a.size}</td>
          <td><span class="tag tag-up num">${a.reduceOnly}</span></td>
          <td><span class="tag tag-up">${a.status}</span></td>
        </tr>`
        )
        .join("");
    } else if (state.activePortfolioTab === "fills") {
      head.innerHTML = `<tr><th>成交时间</th><th>币种</th><th>方向</th><th class="align-right">成交均价</th><th class="align-right">成交张数</th><th class="align-right">手续费</th><th class="align-right">已实现盈亏</th><th>所属策略</th></tr>`;
      body.innerHTML = fillHistory
        .map(
          (f) => `<tr>
          <td class="num muted-text">${f.time}</td>
          <td><b>${f.symbol}</b>-USDT</td>
          <td>${f.side}</td>
          <td class="align-right num">${f.price}</td>
          <td class="align-right num">${f.size}</td>
          <td class="align-right num muted-text">${f.fee}</td>
          <td class="align-right num ${f.pnl.startsWith("+") ? "positive" : f.pnl.startsWith("-") ? "negative" : ""}">${f.pnl}</td>
          <td>${f.strategy}</td>
        </tr>`
        )
        .join("");
    } else if (state.activePortfolioTab === "ledger") {
      head.innerHTML = `<tr><th>时间</th><th>流水类型</th><th>关联合约</th><th class="align-right">变动金额</th><th class="align-right">变动后账户余额</th></tr>`;
      body.innerHTML = ledgerEntries
        .map(
          (l) => `<tr>
          <td class="num muted-text">${l.time}</td>
          <td>${l.type}</td>
          <td class="num">${l.symbol}</td>
          <td class="align-right num ${l.amount.startsWith("+") ? "positive" : "negative"}">${l.amount}</td>
          <td class="align-right num">${l.balance}</td>
        </tr>`
        )
        .join("");
    }
  }

  function promptClosePosition(posId) {
    const p = positions.find((x) => x.id === posId);
    if (!p) return;
    openActionDialog({
      title: `市价平仓确认 · ${p.symbol}-USDT 永续`,
      bodyHtml: `
        <div class="detail-row"><span>持仓方向与数量</span><strong class="num">${p.side === "long" ? "多头" : "空头"} · ${p.size} 张</strong></div>
        <div class="detail-row"><span>开仓均价 / 当前标记价</span><strong class="num">${p.entry} / ${p.mark}</strong></div>
        <div class="detail-row"><span>预计结算盈亏</span><strong class="num ${p.pnl >= 0 ? "positive" : "negative"}">${p.pnl >= 0 ? "+" : ""}${p.pnl.toFixed(2)} USDT</strong></div>
      `,
      footerHtml: `
        <button class="secondary-button" id="cancelClosePosBtn">取消</button>
        <button class="danger-button strong" id="confirmClosePosBtn">确认市价全平</button>
      `,
      onMount: (dlg) => {
        dlg.querySelector("#cancelClosePosBtn")?.addEventListener("click", closeActionDialog);
        dlg.querySelector("#confirmClosePosBtn")?.addEventListener("click", () => {
          const idx = positions.findIndex((x) => x.id === posId);
          if (idx >= 0) positions.splice(idx, 1);
          renderOverviewPositions();
          renderPortfolioTable();
          document.getElementById("kpiActiveSlots").textContent = String(positions.length);
          document.getElementById("ptabPosCount").textContent = String(positions.length);
          closeActionDialog();
          showToast(`已平仓 ${p.symbol}-USDT ${p.size} 张，结算盈亏 ${p.pnl >= 0 ? "+" : ""}${p.pnl.toFixed(2)} USDT`, "up");
        });
      }
    });
  }

  function moveStopToBreakeven(posId) {
    const p = positions.find((x) => x.id === posId);
    if (!p) return;
    const buffer = p.side === "long" ? 1.0012 : 0.9988;
    p.stop = Number((p.entry * buffer).toFixed(p.symbol === "BTC" ? 1 : 2));
    p.status = "已移至保本止损";
    renderOverviewPositions();
    renderPortfolioTable();
    showToast(`已将 ${p.symbol}-USDT 保护止损移至保本价 ${p.stop} (含 0.12% 手续费缓冲)`, "up");
  }

  // ==================== 8. STRATEGIES, BACKTEST & RISK LOGS ====================
  function renderStrategies() {
    const container = document.getElementById("strategyRowsContainer");
    if (!container) return;
    container.innerHTML = strategiesList
      .map(
        (s, idx) => `<div class="strategy-row">
        <div>
          <span class="strategy-name"><b>${s.code}</b>${s.name}<small>${s.tag}</small></span>
          <div class="muted-text" style="font-size:11px;margin-top:2px">${s.desc}</div>
        </div>
        <button class="toggle ${s.on ? "is-on" : ""}" data-toggle-stg="${idx}" aria-label="切换策略开关"></button>
        <div class="weight"><i style="width:${s.weight}%"></i></div>
        <span class="num">${s.weight}%</span>
        <span class="num ${s.pnl30d.startsWith("+") ? "positive" : "negative"}">30D ${s.pnl30d}</span>
        <span class="num">胜率 ${s.winRate}</span>
        <span class="num muted-text">${s.trades} 笔</span>
        <button class="table-action" data-edit-stg="${idx}">参数与审计</button>
      </div>`
      )
      .join("");

    container.querySelectorAll("[data-toggle-stg]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = Number(btn.dataset.toggleStg);
        const stg = strategiesList[idx];
        if (!stg) return;
        stg.on = !stg.on;
        renderStrategies();
        showToast(`${stg.code} ${stg.name} 已${stg.on ? "启用" : "停用"} (已记录审计日志)`, stg.on ? "up" : "warn");
      });
    });

    container.querySelectorAll("[data-edit-stg]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const stg = strategiesList[Number(btn.dataset.editStg)];
        if (!stg) return;
        openActionDialog({
          title: `策略参数配置与 Diff 审计 · ${stg.code} ${stg.name}`,
          bodyHtml: `
            <div class="form-field"><label>资金分配权重 (%)</label><input class="form-input num" id="stgWeightInput" value="${stg.weight}" /></div>
            <div class="form-field"><label>止盈分批规则</label><input class="form-input num" value="1.5R 平 50% + 2.0×ATR 追踪 (小资金不拆单时 1R 保本 + 2R 止盈)" readonly /></div>
            <div class="detail-row"><span>审计要求</span><strong class="muted-text">修改保存后自动写入 audit_logs</strong></div>
          `,
          footerHtml: `
            <button class="secondary-button" id="cancelStgEdit">取消</button>
            <button class="primary-button" id="saveStgEdit">确认修改并写入审计</button>
          `,
          onMount: (dlg) => {
            dlg.querySelector("#cancelStgEdit")?.addEventListener("click", closeActionDialog);
            dlg.querySelector("#saveStgEdit")?.addEventListener("click", () => {
              const newW = Number(dlg.querySelector("#stgWeightInput")?.value);
              if (!Number.isNaN(newW) && newW >= 0 && newW <= 100) stg.weight = newW;
              renderStrategies();
              closeActionDialog();
              showToast(`已更新 ${stg.code} 策略参数并写入审计日志`, "up");
            });
          }
        });
      });
    });
  }

  function renderBacktestHeatmap() {
    const grid = document.getElementById("btHeatmapGrid");
    if (!grid) return;
    const months = ["1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月"];
    const rows = [
      { year: "2026", vals: [4.2, 6.8, -1.4, 5.1, 3.9, -0.8, 7.4, 4.6, 3.2, 5.8, null, null] },
      { year: "2025", vals: [3.1, -2.1, 5.4, 8.2, 4.0, 1.9, -1.2, 6.3, 4.8, 7.1, 3.5, 4.9] },
      { year: "2024", vals: [2.4, 9.1, 6.5, -2.8, 3.7, 1.2, 4.4, -0.9, 5.2, 6.0, 8.4, 3.8] }
    ];

    let html = `<div class="muted-text">年份</div>` + months.map((m) => `<div class="align-center muted-text">${m}</div>`).join("");
    rows.forEach((r) => {
      html += `<div style="display:flex;align-items:center;font-weight:600;color:var(--text-primary)">${r.year}</div>`;
      r.vals.forEach((v) => {
        if (v === null) {
          html += `<div class="heatmap-cell muted-text" style="background:var(--bg-surface-2)">—</div>`;
        } else {
          const isPos = v >= 0;
          const alpha = Math.min(0.42, Math.max(0.08, Math.abs(v) / 18)).toFixed(2);
          const bg = isPos ? `rgba(0, 245, 160, ${alpha})` : `rgba(255, 77, 94, ${alpha})`;
          const color = isPos ? "var(--up)" : "var(--down)";
          html += `<div class="heatmap-cell" style="background:${bg};color:${color}">${isPos ? "+" : ""}${v.toFixed(1)}%</div>`;
        }
      });
    });
    grid.innerHTML = html;
  }

  function renderLogs() {
    const stream = document.getElementById("liveLogStream");
    if (!stream) return;
    const filtered = liveLogs.filter((l) => state.logLevel === "ALL" || l.level === state.logLevel);
    stream.innerHTML = filtered
      .map((l) => {
        const cls = l.level === "ERROR" ? "negative" : l.level === "WARN" ? "warn-text" : "muted-text";
        return `<div class="log-entry">
          <span class="num muted-text">${l.time}</span>
          <strong class="${cls}">${l.level}</strong>
          <span class="tag tag-neutral num">${l.mod}</span>
          <span>${l.msg}</span>
        </div>`;
      })
      .join("");
  }

  // ==================== 9. 60FPS RAF LIVE MARKET TICKER ENGINE ====================
  let lastTickTime = 0;

  function tickMarketSimulation(now) {
    requestAnimationFrame(tickMarketSimulation);
    if (document.hidden) return;
    if (now - lastTickTime < 1400) return;
    lastTickTime = now;

    safeExec(() => {
      // Pick 2 random candidates to micro-tick
      for (let i = 0; i < 2; i++) {
        const idx = Math.floor(Math.random() * candidates.length);
        const c = candidates[idx];
        if (!c) continue;
        const deltaPct = (Math.random() - 0.485) * 0.0022;
        const oldPrice = c.price;
        c.price = Number((c.price * (1 + deltaPct)).toFixed(c.decimals));
        if (c.price === oldPrice) continue;
        const isUp = c.price > oldPrice;
        const flashCls = isUp ? "flash-up" : "flash-down";

        // Update any visible DOM cells for this symbol
        document.querySelectorAll(`[data-cell-price="${c.symbol}"], [data-top-price="${c.symbol}"]`).forEach((el) => {
          el.textContent = formatPrice(c.price, c.decimals);
          el.classList.remove("flash-up", "flash-down");
          void el.offsetWidth;
          el.classList.add(flashCls);
        });

        if (state.view === "workspace" && state.activeSymbol === c.symbol) {
          const wsPriceEl = document.getElementById("wsLivePrice");
          if (wsPriceEl) {
            wsPriceEl.textContent = formatPrice(c.price, c.decimals);
            wsPriceEl.classList.remove("flash-up", "flash-down");
            void wsPriceEl.offsetWidth;
            wsPriceEl.classList.add(flashCls);
          }
        }

        // Sync active position mark price & unrealized PnL
        const pos = positions.find((p) => p.symbol === c.symbol);
        if (pos) {
          pos.mark = c.price;
          const diff = pos.side === "long" ? pos.mark - pos.entry : pos.entry - pos.mark;
          pos.pnlPct = ((diff / pos.entry) * 100 * 3);
          pos.pnl = Number((pos.pnlPct * 1.8).toFixed(2));
          document.querySelectorAll(`[data-pos-mark="${pos.symbol}"]`).forEach((el) => {
            el.textContent = formatPrice(pos.mark, c.decimals);
          });
          document.querySelectorAll(`[data-pos-pnl="${pos.symbol}"]`).forEach((el) => {
            const s = pos.pnl >= 0 ? "+" : "";
            el.textContent = `${s}${pos.pnl.toFixed(2)} (${s}${pos.pnlPct.toFixed(2)}%)`;
            el.className = `align-right num ${pos.pnl >= 0 ? "positive" : "negative"}`;
          });
        }
      }

      // Update total unrealized PnL & equity on overview
      const totalUnrealized = positions.reduce((acc, p) => acc + p.pnl, 0);
      const eqVal = 12438.34 + totalUnrealized;
      const eqEl = document.getElementById("kpiEquityVal");
      if (eqEl) eqEl.textContent = formatPrice(eqVal, 2);
      const unEl = document.getElementById("kpiUnrealizedPnl");
      if (unEl) {
        unEl.textContent = `${totalUnrealized >= 0 ? "+" : ""}${totalUnrealized.toFixed(2)}`;
        unEl.className = `kpi-value num ${totalUnrealized >= 0 ? "positive" : "negative"}`;
      }

      // Update WebSocket latency jitter
      const lat = 22 + Math.floor(Math.random() * 14);
      const latEl = document.getElementById("wsLatency");
      if (latEl) latEl.textContent = `${lat}ms`;
    });
  }

  // ==================== 10. COMMAND PALETTE (⌘K) & KEYBOARD SHORTCUTS ====================
  const commandItems = [
    { icon: "↗", label: "切换到 全景总览 (Overview)", kbd: "G O", run: () => setView("overview") },
    { icon: "⌁", label: "切换到 全市场选币器 (Screener)", kbd: "G S", run: () => setView("screener") },
    { icon: "⌘", label: "切换到 K 线背离工作台 (Workspace)", kbd: "G W", run: () => setView("workspace") },
    { icon: "$", label: "切换到 持仓与实盘待确认信号 (Positions)", kbd: "G P", run: () => setView("positions") },
    { icon: "⚙", label: "切换到 策略与 AI 参数进化 (Strategies)", kbd: "G T", run: () => setView("strategies") },
    { icon: "◷", label: "切换到 事件驱动回测实验室 (Backtest)", kbd: "G B", run: () => setView("backtest") },
    { icon: "!", label: "切换到 多级风控与实时日志 (Risk & Logs)", kbd: "G R", run: () => setView("risk") },
    { icon: "↻", label: "立即触发 4H 全市场选币扫描", kbd: "Scan", run: () => triggerMarketScan() },
    { icon: "◐", label: "切换 紧凑 / 舒适 界面密度", kbd: "D", run: () => document.getElementById("densityToggle")?.click() },
    { icon: "⚡", label: "切换 模拟盘 / 实盘半自动 预览模式", kbd: "Mode", run: () => document.getElementById("modeToggleBtn")?.click() }
  ];

  function renderCommandList(query = "") {
    const listEl = document.getElementById("commandList");
    if (!listEl) return;
    const q = query.trim().toLowerCase();
    const symCommands = candidates
      .filter((c) => !q || c.symbol.toLowerCase().includes(q) || c.divergence.includes(q))
      .slice(0, 6)
      .map((c) => ({
        icon: "◈",
        label: `打开 K 线工作台 · ${c.symbol}-USDT (${c.pool}池 · ${c.divergence} ${c.score}分)`,
        kbd: `${formatPrice(c.price, c.decimals)}`,
        run: () => openSymbolInWorkspace(c.symbol)
      }));

    const navCommands = commandItems.filter((item) => !q || item.label.toLowerCase().includes(q));
    const combined = [...symCommands, ...navCommands];

    listEl.innerHTML = combined
      .map(
        (item, idx) => `<button data-cmd-idx="${idx}" class="${idx === 0 ? "is-active" : ""}">
        <span class="num">${item.icon}</span>
        <span>${item.label}</span>
        <kbd>${item.kbd}</kbd>
      </button>`
      )
      .join("");

    listEl.querySelectorAll("button[data-cmd-idx]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const item = combined[Number(btn.dataset.cmdIdx)];
        document.getElementById("commandDialog")?.close();
        if (item) item.run();
      });
    });
  }

  function openCommandPalette() {
    safeExec(() => {
      const dlg = document.getElementById("commandDialog");
      const inp = document.getElementById("commandInput");
      if (!dlg || !inp) return;
      inp.value = "";
      renderCommandList("");
      if (!dlg.open) dlg.showModal();
      window.setTimeout(() => inp.focus(), 30);
    });
  }

  function triggerMarketScan() {
    showToast("4H 选币扫描已启动 · 正在并发校验 165 个 USDT 永续合约…", "up");
    window.setTimeout(() => {
      state.scanCountdownSec = 14400;
      liveLogs.unshift({
        time: new Date().toTimeString().slice(0, 8),
        level: "INFO",
        mod: "SCR",
        msg: "手动触发全市场扫描完成：候选池 A:4, B:2, C:3, D:1, E:2，耗时 1.2s (Redis 缓存加速)。"
      });
      if (state.view === "risk") renderLogs();
      showToast("选币扫描完成：A 池 4 个，B 池 2 个，最高分 SUI (85分)", "up");
    }, 900);
  }

  // ==================== 11. EVENT BINDINGS & INITIALIZATION ====================
  function bindEvents() {
    // Setup native dialog light-dismiss fallbacks
    setupDialogLightDismiss(document.getElementById("commandDialog"));
    setupDialogLightDismiss(document.getElementById("actionDialog"));
    document.getElementById("closeActionDialogBtn")?.addEventListener("click", closeActionDialog);
    document.getElementById("closeDrawerBtn")?.addEventListener("click", closeScreenerDrawer);

    // Main nav & mobile nav
    document.querySelectorAll("[data-view]").forEach((btn) => {
      btn.addEventListener("click", () => setView(btn.dataset.view));
    });
    document.querySelectorAll("[data-route]").forEach((btn) => {
      btn.addEventListener("click", () => setView(btn.dataset.route));
    });
    document.querySelectorAll("[data-jump-symbol]").forEach((btn) => {
      btn.addEventListener("click", () => openSymbolInWorkspace(btn.dataset.jumpSymbol));
    });

    // Density toggle
    document.getElementById("densityToggle")?.addEventListener("click", (ev) => {
      const next = document.body.dataset.density === "comfortable" ? "compact" : "comfortable";
      document.body.dataset.density = next;
      state.density = next;
      ev.currentTarget.textContent = next === "comfortable" ? "紧凑密度" : "舒适密度";
      showToast(`已切换至${next === "comfortable" ? "舒适" : "紧凑"}显示密度`, "up");
    });

    // Mode toggle (Demo vs Live preview, §17.4 F3)
    document.getElementById("modeToggleBtn")?.addEventListener("click", () => {
      state.mode = state.mode === "demo" ? "live" : "demo";
      document.body.dataset.mode = state.mode;
      const badgeTxt = document.getElementById("modeBadgeText");
      const dot = document.getElementById("modeDot");
      if (state.mode === "live") {
        if (badgeTxt) badgeTxt.textContent = "实盘 · 半自动确认";
        if (dot) dot.className = "status-dot danger";
        showToast("已切换至【实盘 · 半自动确认】视图模式 (顶栏红色安全警戒线已激活)", "down");
      } else {
        if (badgeTxt) badgeTxt.textContent = "模拟盘 · 全自动";
        if (dot) dot.className = "status-dot warn";
        showToast("已切换回【OKX 模拟盘 · 全自动】安全运行模式", "warn");
      }
      if (state.view === "workspace") renderWorkspaceDetail(candidates.find((c) => c.symbol === state.activeSymbol));
    });

    // Scan buttons
    document.getElementById("scanButton")?.addEventListener("click", triggerMarketScan);
    document.querySelectorAll('[data-action="scan"]').forEach((b) => b.addEventListener("click", triggerMarketScan));

    // Overview equity curve tabs & benchmark toggle
    document.querySelectorAll("#equityRangeTabs .segment").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.equityRange = btn.dataset.range;
        document.querySelectorAll("#equityRangeTabs .segment").forEach((b) => b.classList.toggle("is-active", b === btn));
        renderEquityChart();
      });
    });
    document.getElementById("toggleBenchLine")?.addEventListener("click", (ev) => {
      state.showBench = !state.showBench;
      ev.currentTarget.classList.toggle("is-checked", state.showBench);
      renderEquityChart();
    });

    // Overview pool tabs
    document.querySelectorAll("#overviewPoolTabs .pool-tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.overviewPool = btn.dataset.ovPool;
        document.querySelectorAll("#overviewPoolTabs .pool-tab").forEach((b) => b.classList.toggle("is-active", b === btn));
        renderOverviewScreener();
      });
    });

    // Screener controls
    document.querySelectorAll("#screenerPoolTabs .pool-tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.screenerPool = btn.dataset.pool;
        document.querySelectorAll("#screenerPoolTabs .pool-tab").forEach((b) => b.classList.toggle("is-active", b === btn));
        renderScreenerTable();
      });
    });
    document.getElementById("screenerSearchInput")?.addEventListener("input", (ev) => {
      state.screenerSearch = ev.target.value;
      renderScreenerTable();
    });
    document.querySelectorAll("#filterTrendGroup .filter-chip").forEach((btn) => {
      btn.addEventListener("click", () => {
        const t = btn.dataset.trend;
        if (state.screenerTrends.has(t) && state.screenerTrends.size > 1) state.screenerTrends.delete(t);
        else state.screenerTrends.add(t);
        btn.classList.toggle("is-checked", state.screenerTrends.has(t));
        renderScreenerTable();
      });
    });
    document.querySelectorAll("#filterDivGroup .filter-chip").forEach((btn) => {
      btn.addEventListener("click", () => {
        const d = btn.dataset.div;
        if (state.screenerDivs.has(d) && state.screenerDivs.size > 1) state.screenerDivs.delete(d);
        else state.screenerDivs.add(d);
        btn.classList.toggle("is-checked", state.screenerDivs.has(d));
        renderScreenerTable();
      });
    });
    document.getElementById("scoreRangeInput")?.addEventListener("input", (ev) => {
      state.screenerMinScore = Number(ev.target.value);
      document.getElementById("scoreRangeLabel").textContent = `≥ ${state.screenerMinScore}`;
      renderScreenerTable();
    });
    document.getElementById("filterDiffOnly")?.addEventListener("click", (ev) => {
      state.screenerDiffOnly = !state.screenerDiffOnly;
      ev.currentTarget.classList.toggle("is-checked", state.screenerDiffOnly);
      renderScreenerTable();
    });
    document.getElementById("filterOrder2Only")?.addEventListener("click", (ev) => {
      state.screenerOrder2Only = !state.screenerOrder2Only;
      ev.currentTarget.classList.toggle("is-checked", state.screenerOrder2Only);
      renderScreenerTable();
    });
    document.getElementById("filterEarlyOnly")?.addEventListener("click", (ev) => {
      state.screenerEarlyOnly = !state.screenerEarlyOnly;
      ev.currentTarget.classList.toggle("is-checked", state.screenerEarlyOnly);
      renderScreenerTable();
    });
    document.getElementById("resetFilterBtn")?.addEventListener("click", resetScreenerFilters);

    document.querySelectorAll("#screenerTable th.sortable").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.sort;
        if (state.screenerSortKey === key) state.screenerSortAsc = !state.screenerSortAsc;
        else {
          state.screenerSortKey = key;
          state.screenerSortAsc = false;
        }
        renderScreenerTable();
      });
    });

    // Workspace controls
    document.querySelectorAll("#wsPoolTabs button").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.wsPool = btn.dataset.wsPool;
        document.querySelectorAll("#wsPoolTabs button").forEach((b) => b.classList.toggle("is-active", b === btn));
        renderWorkspaceCandidates();
      });
    });
    document.getElementById("wsSearchInput")?.addEventListener("input", (ev) => {
      state.wsSearch = ev.target.value;
      renderWorkspaceCandidates();
    });
    document.querySelectorAll("#wsTimeframeTabs .timeframe").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.timeframe = btn.dataset.tf;
        document.querySelectorAll("#wsTimeframeTabs .timeframe").forEach((b) => b.classList.toggle("is-active", b === btn));
        renderWorkspace();
      });
    });
    document.getElementById("toggleSmaBtn")?.addEventListener("click", (ev) => {
      state.showSma = !state.showSma;
      ev.currentTarget.classList.toggle("is-checked", state.showSma);
      renderWorkspace();
    });
    document.getElementById("toggleEmaBtn")?.addEventListener("click", (ev) => {
      state.showEma = !state.showEma;
      ev.currentTarget.classList.toggle("is-checked", state.showEma);
      renderWorkspace();
    });
    document.getElementById("togglePlanBtn")?.addEventListener("click", (ev) => {
      state.showPlan = !state.showPlan;
      ev.currentTarget.classList.toggle("is-checked", state.showPlan);
      renderWorkspace();
    });
    document.getElementById("annotateModeBtn")?.addEventListener("click", (ev) => {
      state.annotateMode = !state.annotateMode;
      state.annotateClicks = [];
      ev.currentTarget.classList.toggle("primary-button", state.annotateMode);
      document.getElementById("annotationBanner")?.classList.toggle("is-visible", state.annotateMode);
      if (state.annotateMode) {
        document.getElementById("annotationStepText").textContent = "当前进度：请点击 MACD 第 1 段柱子 G1 (0/2)";
        showToast("已进入金标样本标注模式：请依次点击 MACD 副图上的两段柱子", "warn");
      }
    });
    document.getElementById("fullscreenChartBtn")?.addEventListener("click", () => {
      document.getElementById("workspaceLayout")?.classList.toggle("is-fullscreen");
    });
    document.querySelectorAll("#wsDockTabs .pool-tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.activeDockTab = btn.dataset.dock;
        renderWorkspaceDock();
      });
    });

    // Bidirectional hover link between right metrics and chart divergence lines (§17.9 F11)
    document.querySelectorAll("#detailMathMetrics .detail-row").forEach((row) => {
      row.addEventListener("pointerenter", () => {
        document.getElementById("marketChart")?.classList.add("is-linked-hover");
      });
      row.addEventListener("pointerleave", () => {
        document.getElementById("marketChart")?.classList.remove("is-linked-hover");
      });
    });

    // Thumbs up / down feedback
    document.getElementById("btnFeedbackUp")?.addEventListener("click", (ev) => {
      ev.currentTarget.classList.add("is-selected-up");
      document.getElementById("btnFeedbackDown")?.classList.remove("is-selected-down");
      showToast(`已记录【✓ 算对了】校准样本 (${state.activeSymbol}-USDT 4H)，写入 divergence_feedback`, "up");
    });
    document.getElementById("btnFeedbackDown")?.addEventListener("click", (ev) => {
      ev.currentTarget.classList.add("is-selected-down");
      document.getElementById("btnFeedbackUp")?.classList.remove("is-selected-up");
      showToast(`已记录【✗ 算错了】负样本 (${state.activeSymbol}-USDT 4H)，供周日 Agent 4 归因复盘`, "warn");
    });

    // Workspace Trade execution
    document.getElementById("wsTradeBtn")?.addEventListener("click", () => {
      if (state.haltNewOrders) {
        showToast("下单被拦截：当前处于【停止开新仓】风控限制状态", "down");
        return;
      }
      const c = candidates.find((x) => x.symbol === state.activeSymbol);
      if (!c) return;
      const isBull = !c.divergence.includes("顶");
      openOrders.unshift({
        clOrdId: `S1${Date.now()}`,
        symbol: c.symbol,
        side: isBull ? "long" : "short",
        type: "限价突破",
        price: formatPrice(c.entry, c.decimals),
        size: c.size,
        filled: "0%",
        stop: formatPrice(c.stop, c.decimals),
        status: "LIVE (附带止损)",
        time: new Date().toTimeString().slice(0, 8)
      });
      state.activeDockTab = "orders";
      renderWorkspaceDock();
      showToast(`已提交 ${isBull ? "开多" : "开空"} ${c.symbol}-USDT ${c.size} 张 @ ${formatPrice(c.entry, c.decimals)} (附带止损 ${formatPrice(c.stop, c.decimals)})`, "up");
    });

    // Portfolio tabs & actions
    document.querySelectorAll("#portfolioTabs button").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.activePortfolioTab = btn.dataset.ptab;
        renderPortfolioTable();
      });
    });
    document.getElementById("reconcileNowBtn")?.addEventListener("click", () => {
      showToast("OKX 账本实时对账通过：持仓、挂单、保证金余额 100% 一致 (差异 0)", "up");
    });
    document.getElementById("exportCsvBtn")?.addEventListener("click", () => {
      showToast("已生成并导出 divergence_quant_fills_20261024.csv", "up");
    });

    // AI Proposal actions
    document.getElementById("approveProposalBtn")?.addEventListener("click", () => {
      openActionDialog({
        title: "批准并应用 AI 参数优化提案 (#2026-W43)",
        bodyHtml: `
          <div class="detail-row"><span>修改项 1</span><strong class="num">divergence.volume_ratio: <em class="negative">0.80</em> → <em class="positive">0.72</em></strong></div>
          <div class="detail-row"><span>修改项 2</span><strong class="num">divergence.area_ratio: <em class="negative">0.70</em> → <em class="positive">0.66</em></strong></div>
          <div class="detail-row"><span>样本外 60D 验证</span><strong class="positive num">回撤 -1.8% · 夏普 +14.2%</strong></div>
        `,
        footerHtml: `
          <button class="secondary-button" id="cancelApproveBtn">取消</button>
          <button class="primary-button" id="confirmApproveBtn">确认写入配置并记录审计</button>
        `,
        onMount: (dlg) => {
          dlg.querySelector("#cancelApproveBtn")?.addEventListener("click", closeActionDialog);
          dlg.querySelector("#confirmApproveBtn")?.addEventListener("click", () => {
            const badge = document.getElementById("proposalStatusBadge");
            if (badge) {
              badge.textContent = "APPLIED · 已人工批准生效 (v1.4.2)";
              badge.className = "tag tag-up";
            }
            closeActionDialog();
            showToast("已批准 Agent 4 参数提案并写入 config.yaml 与审计日志", "up");
          });
        }
      });
    });
    document.getElementById("rejectProposalBtn")?.addEventListener("click", () => {
      const badge = document.getElementById("proposalStatusBadge");
      if (badge) {
        badge.textContent = "REJECTED · 已人工驳回";
        badge.className = "tag tag-down";
      }
      showToast("已驳回本期 AI 参数提案，保持现行参数不变", "warn");
    });
    document.getElementById("rollbackProposalBtn")?.addEventListener("click", () => {
      showToast("已回滚至上一版稳定参数快照 (v1.4.1)", "up");
    });
    document.getElementById("runAiProposalBtn")?.addEventListener("click", () => {
      showToast("Agent 4 正在拉取近 30 天错单与金标样本执行 Walk-Forward 网格校准…", "up");
    });

    // Backtest run button
    document.getElementById("runBacktestBtn")?.addEventListener("click", (ev) => {
      const btn = ev.currentTarget;
      btn.textContent = "回测撮合计算中 (300币池)…";
      btn.disabled = true;
      window.setTimeout(() => {
        btn.disabled = false;
        btn.textContent = "启动逐 K 线回测";
        document.getElementById("btStatusMeta").textContent = `最近任务：#BT-205 已完成 (耗时 2.8s)`;
        showToast("事件驱动回测完成：SMA200 年化 +48.6%，优于 EMA200 (+43.1%)", "up");
      }, 750);
    });

    // Risk & Kill Switch controls
    document.querySelectorAll("#logLevelTabs .segment").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.logLevel = btn.dataset.level;
        document.querySelectorAll("#logLevelTabs .segment").forEach((b) => b.classList.toggle("is-active", b === btn));
        renderLogs();
      });
    });
    document.getElementById("haltNewOrdersBtn")?.addEventListener("click", (ev) => {
      state.haltNewOrders = !state.haltNewOrders;
      document.body.dataset.halt = String(state.haltNewOrders);
      ev.currentTarget.textContent = state.haltNewOrders ? "恢复允许开新仓" : "停止开新仓";
      document.getElementById("footerRiskText").textContent = state.haltNewOrders ? "限制开新仓" : "正常放行";
      document.getElementById("footerRiskText").className = state.haltNewOrders ? "warn-text" : "positive";
      showToast(state.haltNewOrders ? "已开启【停止开新仓】：新信号将被拦截，在途持仓止损继续生效" : "已恢复正常开新仓权限", state.haltNewOrders ? "warn" : "up");
    });

    document.getElementById("killSwitchModalBtn")?.addEventListener("click", () => {
      openActionDialog({
        title: "紧急熔断：全部平仓并停机 (Kill Switch)",
        bodyHtml: `
          <p class="negative">警告：该操作将立即撤销全部未成交委托，并以市价平掉当前全部 ${positions.length} 个在途持仓，同时锁定交易引擎。</p>
          <div class="form-field">
            <label>请输入大写 STOP 解锁下方长按按钮</label>
            <input class="form-input num" id="killStopInput" placeholder="在此输入 STOP" autocomplete="off" />
          </div>
        `,
        footerHtml: `
          <button class="secondary-button" id="cancelKillBtn">取消</button>
          <button class="danger-button strong hold-button" id="confirmKillHoldBtn" disabled>
            <span class="hold-progress"></span>
            <span style="position:relative;z-index:1">长按 1.5 秒执行全平停机</span>
          </button>
        `,
        onMount: (dlg) => {
          const inp = dlg.querySelector("#killStopInput");
          const holdBtn = dlg.querySelector("#confirmKillHoldBtn");
          dlg.querySelector("#cancelKillBtn")?.addEventListener("click", closeActionDialog);
          inp?.addEventListener("input", () => {
            holdBtn.disabled = inp.value.trim() !== "STOP";
          });
          bindHoldToConfirm(holdBtn, 1400, () => {
            positions.length = 0;
            openOrders.length = 0;
            state.haltNewOrders = true;
            document.body.dataset.halt = "true";
            renderOverviewPositions();
            renderPortfolioTable();
            document.getElementById("kpiActiveSlots").textContent = "0";
            document.getElementById("footerRiskText").textContent = "已紧急停机";
            document.getElementById("footerRiskText").className = "negative";
            closeActionDialog();
            showToast("已执行【全部平仓并停机】，所有仓位已市价清空并发送告警邮件", "down");
          });
        }
      });
    });

    // Command Palette & Shortcuts Help
    document.getElementById("commandTrigger")?.addEventListener("click", openCommandPalette);
    document.getElementById("commandInput")?.addEventListener("input", (ev) => renderCommandList(ev.target.value));
    document.getElementById("shortcutHelpBtn")?.addEventListener("click", showShortcutHelpModal);

    // Global Keyboard Navigation (§17.12 F8)
    document.addEventListener("keydown", (ev) => {
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "k") {
        ev.preventDefault();
        openCommandPalette();
        return;
      }
      const tag = document.activeElement?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;

      const key = ev.key.toLowerCase();
      if (key === "d") {
        ev.preventDefault();
        document.getElementById("densityToggle")?.click();
      } else if (key === "f" && state.view === "workspace") {
        ev.preventDefault();
        document.getElementById("fullscreenChartBtn")?.click();
      } else if (key === "m" && state.view === "workspace") {
        ev.preventDefault();
        document.getElementById("annotateModeBtn")?.click();
      } else if (["1", "2", "3", "4"].includes(key) && state.view === "workspace") {
        const tfMap = { "1": "15m", "2": "1H", "3": "4H", "4": "1D" };
        state.timeframe = tfMap[key];
        document.querySelectorAll("#wsTimeframeTabs .timeframe").forEach((b) => b.classList.toggle("is-active", b.dataset.tf === state.timeframe));
        renderWorkspace();
      } else if (key === "j" || key === "k") {
        ev.preventDefault();
        const step = key === "j" ? 1 : -1;
        if (state.view === "workspace") {
          const idx = candidates.findIndex((c) => c.symbol === state.activeSymbol);
          const next = candidates[(idx + step + candidates.length) % candidates.length];
          if (next) {
            state.activeSymbol = next.symbol;
            renderWorkspace();
          }
        } else if (state.view === "screener") {
          const list = getFilteredScreenerList();
          if (list.length > 0) {
            const idx = list.findIndex((c) => c.symbol === state.selectedScreenerSymbol);
            const next = list[(idx + step + list.length) % list.length];
            if (next) {
              state.selectedScreenerSymbol = next.symbol;
              renderScreenerTable();
              openScreenerDrawer(next.symbol);
            }
          }
        }
      } else if (key === "enter" && state.view === "screener") {
        openSymbolInWorkspace(state.selectedScreenerSymbol);
      } else if (key === "escape") {
        closeScreenerDrawer();
      } else if (ev.key === "?") {
        showShortcutHelpModal();
      } else if (key === "g") {
        const handleSecond = (ev2) => {
          const map = { o: "overview", s: "screener", w: "workspace", p: "positions", t: "strategies", b: "backtest", r: "risk" };
          const target = map[ev2.key.toLowerCase()];
          if (target) setView(target);
        };
        window.addEventListener("keydown", handleSecond, { once: true });
      }
    });
  }

  function showShortcutHelpModal() {
    openActionDialog({
      title: "全局键盘快捷键速查 (§17.12)",
      bodyHtml: `
        <div class="detail-row"><span>唤起全局命令与币种搜索</span><kbd class="num">⌘K / Ctrl+K</kbd></div>
        <div class="detail-row"><span>快速跳转 7 大核心视图</span><kbd class="num">G → O / S / W / P / T / B / R</kbd></div>
        <div class="detail-row"><span>选币器 / 工作台上下切换币种</span><kbd class="num">J / K</kbd></div>
        <div class="detail-row"><span>K 线工作台切换周期 (15m/1H/4H/1D)</span><kbd class="num">1 / 2 / 3 / 4</kbd></div>
        <div class="detail-row"><span>工作台图表全屏 / 交互式样本标注</span><kbd class="num">F / M</kbd></div>
        <div class="detail-row"><span>紧凑 / 舒适 密度切换</span><kbd class="num">D</kbd></div>
      `,
      footerHtml: `<button class="primary-button" id="closeHelpModalBtn">知道了</button>`,
      onMount: (dlg) => dlg.querySelector("#closeHelpModalBtn")?.addEventListener("click", closeActionDialog)
    });
  }

  // Clock & Countdown Heartbeat
  function startClockAndCountdown() {
    const formatter = new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false
    });

    const tickSec = () => {
      safeExec(() => {
        const now = new Date();
        const tStr = formatter.format(now).replaceAll("/", "-").replace(",", " ");
        const topTimeEl = document.getElementById("topTime");
        if (topTimeEl) topTimeEl.textContent = tStr;

        state.scanCountdownSec = Math.max(0, state.scanCountdownSec - 1);
        const hrs = String(Math.floor(state.scanCountdownSec / 3600)).padStart(2, "0");
        const mins = String(Math.floor((state.scanCountdownSec % 3600) / 60)).padStart(2, "0");
        const secs = String(state.scanCountdownSec % 60).padStart(2, "0");
        const cdStr = `${hrs}:${mins}:${secs}`;
        const scEl = document.getElementById("statusCountdown");
        if (scEl) scEl.textContent = cdStr;
        const lastEl = document.getElementById("lastScanLabel");
        if (lastEl) lastEl.textContent = `上次扫描 20:00:03 · 下次 ${cdStr}`;

        // Tick pending confirm countdowns
        pendingConfirms.forEach((item) => {
          if (item.remainSec > 0) item.remainSec -= 1;
        });
        if (state.view === "positions") renderConfirmQueue();
      });
    };

    tickSec();
    window.setInterval(tickSec, 1000);
  }

  // Bootstrap Application
  function init() {
    initSpotlightSurfaces();
    renderEquityChart();
    renderOverviewScreener();
    renderOverviewSignals();
    renderOverviewPositions();
    renderScreenerTable();
    renderWorkspace();
    renderConfirmQueue();
    renderPortfolioTable();
    renderStrategies();
    renderBacktestHeatmap();
    renderLogs();
    bindEvents();
    startClockAndCountdown();
    requestAnimationFrame(tickMarketSimulation);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

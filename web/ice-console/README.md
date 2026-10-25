# CHRONO QUANT · 冰透暖白量化控制台

按《加密货币量化交易系统前端设计规范与执行指南》ARCH-SPEC-2026.1 实现的
「冰透银白毛玻璃」高保真控制台样板，并按反馈整体调暖为**香槟暖白**色调。

> 仓库位置：`web/ice-console/`，完全自包含，不依赖后端或 React 工程。

- 单页可直接打开：`index.html`
- 图表：TradingView Lightweight Charts v5.0.8（本地 vendor，无外网依赖）
- 字体：Inter + JetBrains Mono（本地 woff2）
- 纯前端演示数据，未接后端 API

## 预览

- 主视图 1440×900：`preview/console-1440.png`
- 4K 宽屏验收：`preview/console-4k.png`
- 命令面板（玻璃浮层反模式验收）：`preview/console-palette.png`
- 规范验收抽屉：`preview/console-spec.png`

## 运行

直接双击 `index.html` 即可。若浏览器对本地资源有限制，可在 `ice-console` 的上一级目录执行：

```bash
python -m http.server 8811
# 打开 http://127.0.0.1:8811/ice-console/
```

## 规范落点

| 规范 | 实现 |
|---|---|
| Canvas 冷白银基底 | 暖调香槟银 `#FBF8F3 → #F2ECE4`，柔和径向 + 噪点 + 漂移光斑 |
| Glass 68–75% / blur 20px / 顶部 85% 高光 | `.panel`、`.glass`：backdrop-filter blur(20px) saturate(1.8)，顶部 1px 白色镜面描边 |
| 漫反射微阴影 | `0 10px 40px rgba(66,48,28,.045)` + 内高光，无重色实影 |
| Emerald / Coral | 蜡烛与状态严格使用 `#059669 / #E11D48`，文字使用 `#047857 / #BE123C`，无荧光原色 |
| TV Lightweight Charts v5 | `background: transparent`；点线网格 `rgba(139,118,94,.16)`；量能半透明沉底 `top: .82`；十字虚线 + 深色标签 |
| 信号打点 | 微型圆点 `circle`（半径约 4px），买点下方 `#047857`，止盈/止损上方 `#BE123C` |
| 等宽数字协议 | 全局 `JetBrains Mono` + `tabular-nums` + `tnum`；NAV 22px bold；指标名全大写 |
| 价格微闪 / 呼吸灯 | 300ms 背景闪现；6px 翡翠圆点 2.4s 呼吸脉冲 |
| 三栏拓扑 | 12 栅格 3 / 6 / 3：策略矩阵 → 透明 TV 图表 → L2 深度与成交流水 |
| 流动性证据 | 订单簿累计深度渐变条、买卖比、spread；毫秒级成交流水右对齐 |
| 交互 | 标的下拉 / 15m·1H·4H·1D / MA·VOL 开关 / 单 Agent 暂停 / 信号确认拒单 / 全屏 / ⌘K 命令面板 / 规范抽屉 |

## 验收记录

- 1440×900、3840×2160 两档截图：无横向溢出，三栏均完整填充，4K 居中保持紧凑。
- Chrome/Edge 无 console 报错（CDP 采集 Runtime / Log）。
- 已知演示限制：数据为本地生成并实时轻量跳动；未接 OKX / 后端 API；其余导航为演示占位。

## 目录

```
ice-console/
├── index.html
├── assets/ice.css
├── assets/ice.js
├── vendor/lightweight-charts.standalone.production.js
├── fonts/*.woff2
└── preview/*.png
```

# MHD 策略模块（摆动点 MACD 柱背离）

## 因子定义

在 4H 已确认 K 线的摆动高点/低点处，比较价格与 MACD 柱的"同价不同势"：

```text
pm  = (price_b - price_ref) / ATR14_ref
hm  = (hist_b  - hist_ref)  / stdev(hist, 100)_b
D   = pm - hm
```

- `D_pair`：与上一个同类摆动点比较（价格更高但柱子更低 = 看跌背离）
- `D_peak`：与回看窗口内 MACD 柱最极端的摆动点比较（价格回到同一水平但动量衰减）
- `D = max(D_pair, D_peak)`，超过 `min_d` 才产生信号
- 信号只在摆动点确认那根（`i + swing_right`）的收盘生成，不使用未来数据
- 顶部 MHD → 开空；底部 MHD → 开多

## 交易与风控

- 运行周期：`timeframe` 可选 `5m` / `15m` / `1H`（默认）/ `4H` / `1D` / `AUTO`
  - `AUTO`：同时计算 5m、15m、1H、4H、1D，取当前收盘确认的最强 MHD 信号
- 初始止损：入场价 ± `atr_mult × ATR14`
- 持仓期间：ATR 移动止损（多头取入场后最高价，空头取最低价），只向有利方向移动
- 不设固定止盈，由移动止损保护利润
- 默认仓位：账户权益的 **50% 作为保证金**（`position_pct=0.5`）
- 可调仓位倍数：`position_multiplier`（默认 1.0，范围 0.1–3.0）
- 可调合约杠杆：`leverage`（默认 3x，范围 1–20）
- 目标名义价值 = 权益 × `position_pct` × `position_multiplier` × `leverage`
- 内部模拟盘仓位写入 `positions` 表，控制台可实时查看

## 热加载接口

- `GET /api/v1/strategies`：读取 MHD 当前参数与持仓数量
- `PUT /api/v1/strategies/mhd_divergence/params`：保存参数到 `data/strategy_runtime.json`
- 引擎每 30 秒检测运行时文件变化并热重建策略实例，下一轮模拟仓位自动按新参数调仓
- 控制台右上角「策略」按钮可调整仓位比例、仓位倍数、杠杆、ATR 参数

## 安全边界

- 服务默认 `dry_run=True`，仅做内部模拟盘纸面成交，不向 OKX 提交订单
- `LIVE_TRADING_CONFIRM=NO` 保持不变
- 实盘交易仍需完成项目自身的 G1–G4 门禁与人工放行

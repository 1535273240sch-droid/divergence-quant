# M06 HANDOFF

## 状态

已完成 M06 第一版：事件驱动回测内核、成本与撮合、多币种组合账户、Walk-Forward、
参数扫描、逐策略对比和 HTML 报告。代码只落在 `src/dquant/backtest/`、
`reports/backtest/` 和 M06 测试文件，未修改公共契约或其他模块目录。
`协作/00_总控/总进度.md` 由总控维护，本模块未修改。

## 已完成内容

1. `src/dquant/backtest/config.py`
   - `BacktestConfig`：初始权益、执行周期、maker/taker 费率、滑点、杠杆、
     限价单存活 K 线数、交易窗口、时区、随机种子、run_id。
   - K 线周期对齐：1H/4H 按 UTC，1D 按北京时间 00:00 开盘。
   - 配置哈希与确定性 `run_id`。
2. `src/dquant/backtest/data.py`
   - 行情校验：已确认、周期对齐、严格递增、无重复、K 线与交易对匹配。
   - 数据质量摘要与缺失 K 线警示；行情指纹。
   - 按收盘时间/周期优先级/交易对排序的事件构建。
3. `src/dquant/backtest/engine.py`
   - 多交易对共享账户、逐 K 线推进的事件驱动引擎。
   - 信号在收盘产生，下一根执行周期开盘成交；不接受前视信号。
   - 市价单和止损单施加不利滑点并做 tick 对齐；止盈按限价成交。
   - 同根 K 线止损优先于止盈。
   - 支持开仓、全平、减仓、移动止损、限价挂单超时、回测结束强制平仓。
   - 手续费、资金费率、保证金占用、已实现/未实现盈亏全部使用 `Decimal`。
   - 组合状态（权益、可用权益、持仓、待成交订单、日/周起始权益、最新资金费率）
     传给注入的 M08 风控。
4. `src/dquant/backtest/interfaces.py`
   - `StrategyProtocol`、`RiskGateProtocol`、`FeatureProviderProtocol`。
   - `StrategyFeatures`：注入 M03 的 `RegimeResult`、M04 的 `DivergenceSignal`、
     M05 的 `ScreenerCandidate` 及扩展上下文。
   - `BacktestBarContext`：只读已收盘历史、持仓快照、权益、特征和当前 K 线。
   - 上下文历史在创建时冻结，旧上下文无法读取后续 K 线。
5. `src/dquant/backtest/metrics.py`
   - 总收益、年化、最大回撤、夏普、索提诺、卡玛、胜率、盈亏比、交易数、
     平均持仓、手续费、资金费、滑点成本。
   - 回撤曲线、按用户时区聚合的月度收益。
   - 空曲线、零波动、无亏损交易等退化场景显式写入 `notes`。
6. `src/dquant/backtest/walk_forward.py`
   - 训练窗口选参、测试窗口不再优化、样本外资金曲线按窗口资金缩放拼接。
   - 步长不得小于测试窗口，防止样本外重叠。
7. `src/dquant/backtest/optimization.py`、`sweep.py`
   - 参数笛卡尔积、目标函数选择、`min_trades` 过滤、确定性并列排序。
   - 二维参数热力图数据和逐组合完整结果。
8. `src/dquant/backtest/comparison.py`
   - S1/S2/S3 或 SMA200/EMA200 变体的逐策略指标、基准差值和累计收益对比。
   - M06 不计算 MA；变体由 M03 趋势分类和 M07 策略参数生成。
9. `src/dquant/backtest/report.py`
   - 自包含 HTML：指标卡、资金曲线、回撤曲线、月度收益、逐笔交易、
     被拒绝信号、数据质量/偏差警示、复现实验信息。
   - 对比报告、参数扫描热力图报告、Walk-Forward 样本外报告。
   - 所有动态文本 HTML 转义，不依赖 CDN 或外部脚本。
10. `src/dquant/backtest/models.py`、`errors.py`、`history.py`、`costs.py`
    - 回测专有结果模型、异常层次、冻结行情视图、费用/滑点/tick 工具。
11. 测试
    - `tests/unit/test_backtest.py`：40 项。
    - `tests/integration/test_backtest_consistency.py`：8 项。
    - 覆盖正常/空/边界/非法输入、依赖异常、重复运行、止损优先、跳空、
      限价成交与过期、分批止盈、移动止损、减仓、资金费率收付、保证金不足、
      防前视、数据质量、指标、报告转义、Walk-Forward、参数扫描、策略对比。
12. `reports/backtest/.gitkeep`：报告输出目录占位。测试报告写入临时目录，
    不向仓库提交生成物。

## 关键入口

```python
from dquant.backtest import (
    BacktestConfig,
    FeatureRequest,
    RiskRequest,
    StrategyFeatures,
    compare_results,
    render_comparison_report,
    render_report,
    render_sweep_report,
    render_walk_forward_report,
    run_backtest,
    run_parameter_sweep,
    run_walk_forward,
    write_report,
)
```

单次回测：

```python
result = run_backtest(
    data={inst_id: {CandleBar.ONE_HOUR: candles}},
    instruments={inst_id: instrument},
    strategy=strategy,          # M07 实现
    risk_gate=risk_gate,        # M08 适配器
    feature_provider=provider,  # M03/M04/M05 预计算索引
    config=BacktestConfig(initial_equity=Decimal("10000")),
    funding_rates=funding_rates,
)
record = result.as_record()     # 映射 backtest_runs 字段
write_report(result, "reports/backtest/run.html")
```

## 撮合与成本口径

- `Candle.ts_ms` 在本模块解释为 **K 线开盘时间**（UTC 毫秒）；K 线在
  `ts_ms + 周期长度` 收盘后对策略可见。
- 1D 按 OKX `bar=1D` 和北京时间 00:00 开盘对齐。
- 信号 `created_ts_ms` 必须等于当前 K 线收盘时间，否则报错。
- 信号只能在下一根执行周期 K 线开盘成交；执行周期由 `BacktestConfig.execution_bar` 指定。
- 市价开仓/平仓、止损：不利滑点 + tick 不利取整 + taker 费率。
- 止盈：限价成交、maker 费率；若开盘已跳空越过止盈，按更优开盘价成交。
- 限价开仓：盘中触价按限价 maker 成交，开盘即穿越按开盘 taker 成交；
  超过 `limit_order_ttl_bars` 未成交则过期并记录拒绝。
- 同根 K 线同时触及止损与止盈：整笔剩余持仓按止损处理。
- 多空均支持；同一交易对最多一个方向持仓。
- 保证金 = 开仓名义价值 / `leverage`；手续费从钱包扣除；资金费率为正时多头支付、
  空头收取。
- 资金费率点按时间应用；K 线内部的多个费率点使用该 K 线收盘价近似。
- 默认在回测结束时按最后收盘价强制平仓；可配置保留未平仓持仓并输出警示。
- 权益曲线只在执行周期收盘记录；夏普/索提诺按执行周期收益年化。

## 与依赖模块的集成契约

### M03 / M04 / M05 → FeatureProvider

`feature_provider(request: FeatureRequest) -> StrategyFeatures`：

- `FeatureRequest` 提供 `inst_id`、`bar`、`close_ts_ms`、冻结的 `history`。
- `StrategyFeatures` 字段：`regime`、`divergence`、`candidate`、`extras`。
- 提供器必须是纯函数或只读索引；同一 `(inst_id, bar, close_ts_ms)` 的结果会被缓存一次。
- M03/M04/M05 应按确认时点返回 “截至该收盘已确认” 的结果，禁止把未来枢轴或
  未确认 K 线放进特征。
- 选币评分、Top N 和候选池由 M05 负责；M06 只消费 `ScreenerCandidate`，不重新实现
  选币逻辑。策略通过 `context.candidate` 判断是否处理当前候选。
- `extras` 可用于 BTC 大盘过滤、周末系数等尚未进入公共契约的上下文，键名由
  M07/M08 自行约定；M06 不解释其内容。

### M07 → Strategy

- 必须提供非空 `name`、`version` 和 `on_bar(context) -> Sequence[Signal]`；
  `on_fill(context, fill)` 可选。
- 使用 `context.candles()`、`context.position()`、`context.equity`、
  `context.features` / `context.regime` / `context.divergence` / `context.candidate`。
- `context.now_ts_ms` 是收盘时间别名；信号 `created_ts_ms` 必须使用它。
- 开仓信号必须带止损；多头止损低于参考价、空头高于参考价；止盈方向必须正确；
  止损/止盈价格必须按 `Instrument.tick_sz` 对齐；止盈 `fraction` 之和不超过 1。
- 市价信号的 `price` 只作为策略参考价，实际成交价为下一根执行 K 线开盘价；
  限价信号的 `price` 是限价，必须 tick 对齐。
- 同一交易对已有持仓时新开仓会被拒绝；反方向需先平仓。
- CLOSE 平掉全部剩余；REDUCE 数量由 `RiskDecision.qty` 提供；
  MOVE_STOP 只能收紧且方向必须与持仓一致。
- 回测结束那根 K 线仍会调用 `on_bar`，但信号会以 `OUTSIDE_TRADING_WINDOW` 拒绝。

### M08 → RiskGate

`risk_gate.evaluate(request: RiskRequest) -> RiskDecision`：

- `RiskRequest` 包含信号、权益、钱包余额、已用保证金、可用权益、公共 `Position`
  快照、待成交订单、日/周起始权益（北京时间）、信号交易对最新资金费率和时间戳。
- 只对 OPEN/REDUCE 调用风控；CLOSE/MOVE_STOP 属于保护性动作，不等待风控放行。
- 风控返回的 `signal_id` 必须与请求一致。
- 开仓/减仓数量必须按 `lot_sz` 对齐且不小于 `min_sz`；不合法会被拒绝并记录。
- M06 不实现单笔风险、最大持仓、同方向数量、杠杆、日亏损、周亏损、BTC 过滤、
  周末系数或熔断规则；这些属于 M08。M08 需要写一个适配器把内部风控管理器映射到
  `RiskGateProtocol`。

### M02 → 行情

- 只接受 `confirmed=True` 的 K 线；未确认 K 线会直接报错。
- 每个 `(inst_id, bar)` 流必须严格递增、无重复、按周期对齐。
- 允许缺口，但会在报告“数据质量”和 `warnings` 中提示缺失根数。
- 资金费率通过 `FundingRatePoint(ts_ms, rate)` 注入；不提供时按 0 计并输出警示。

### M11 / M12

- `BacktestResult.as_record()` 输出 `backtest_runs` 所需字段；`report_path` 默认为
  `None`，由调用方写入报告后回填。
- `render_report` 等函数返回 HTML 字符串；`write_report`/`write_html` 写文件。
- 逐笔交易 K 线截图不在 M06 生成；M12 可根据交易时间和 `inst_id` 从行情接口
  渲染截图，避免回测结果重复携带完整 K 线数据。

## 公共接口变更申请

以下只是申请，未修改 `src/dquant/contracts/` 或接口契约：

1. **`Candle.ts_ms` 语义**
   - 需要：在 `接口契约.md` 明确 `Candle.ts_ms` 是开盘时间还是收盘时间。
   - 现状：M06 按开盘时间实现；若 M02 最终采用收盘时间，会改变所有信号确认和成交时点。
   - 影响：M02、M03、M04、M06、M07。
2. **策略上下文与特征契约**
   - 需要：把 `BacktestBarContext` / `StrategyFeatures` / `FeatureRequest` 的最小字段
     写入接口契约或 M07 设计文档。
   - 原因：M07 的策略要同时跑实盘和回测，必须使用同一上下文语义。
   - 兼容方案：M07 在自己的 `StrategyContext` 中提供相同字段并适配本模块。
3. **`Signal` 缺少策略版本**
   - 需要：M07 交付标准要求信号包含“策略版本”，但公共 `Signal` 只有 `strategy_name`。
   - 兼容方案：短期由回测元数据记录 `strategy.version`，逐笔交易只记录 `strategy_name`；
     建议后续契约版本为 `Signal` 增加 `strategy_version`。
4. **`Signal` 缺少减仓数量**
   - 需要：REDUCE 动作没有数量字段。
   - 兼容方案：当前由 `RiskDecision.qty` 提供；请确认这一约定，或在后续契约版本增加
     `Signal.size` 并明确与 `RiskDecision.qty` 的优先级。
5. **M06 ↔ M08 风控适配**
   - 需要：M08 确认 `RiskRequest` 字段是否足够实现日/周亏损、BTC 过滤、周末系数和
     资金费率过滤；不足时在接口契约增加相应上下文，而不是在回测模块内复制规则。

## 实际检查

在项目根目录执行：

```text
ruff check src/dquant/backtest tests/unit/test_backtest.py tests/integration/test_backtest_consistency.py
# 通过

ruff format --check src/dquant/backtest tests/unit/test_backtest.py tests/integration/test_backtest_consistency.py
# 通过，17 files already formatted

py -m pytest -q tests/unit/test_backtest.py tests/integration/test_backtest_consistency.py
# 通过，48 passed

py -m pytest -q tests/unit/test_backtest.py tests/integration/test_backtest_consistency.py \
  tests/unit/test_contracts.py tests/unit/test_m01_db.py tests/unit/test_m01_settings.py \
  tests/test_project_layout.py
# 通过，60 passed
```

全量测试当前状态（与 M06 无关，未修改其他模块）：

```text
py -m pytest -q
# 8 failed, 433 passed
# 失败：tests/unit/test_divergence.py 7 项、tests/unit/test_risk.py 1 项
# 属于 M04/M08 正在并行开发的代码，M06 全部测试通过
```

## 已知限制

- M03/M04/M05/M07/M08 尚未交付，测试中的策略、特征和风控均为桩实现；
  尚未完成与真实 S1/S2/S3 信号代码的端到端一致性验证。
- 无订单簿、成交量冲击、部分成交和撮合延迟模拟；限价单不模拟 60 秒撤单，
  只按 `limit_order_ttl_bars` 根执行 K 线存活。
- K 线内部事件顺序不可知：同根 K 线止损优先；限价单盘中成交后也可能用同一根
  K 线的极值判断止损/止盈，属于保守/近似处理。
- 1D K 线内部的多个资金费率点使用收盘价近似，不用逐分钟 Mark Price。
- 未实现独立的训练/验证/测试三段切分；Walk-Forward 的训练窗口用于选参，
  滚动测试窗口承担样本外验证。需要三段切分时可组合 `BacktestConfig` 的
  `start_ts_ms` / `end_ts_ms` 分别运行。
- 多策略对比是各自独立资金的逐策略报告，不计算 S1/S2/S3 共享资金的组合权益；
  共享资金组合需要明确的策略资金分配和组合风控规则。
- HTML 报告不内嵌逐笔交易 K 线截图，截图由 M12 根据交易时间和行情接口生成。
- Walk-Forward 和参数扫描会为每个组合/窗口重新运行完整数据段，长周期大数据下
  耗时较高；后续可在 M05/M10 侧增加缓存或切片优化。
- 全量测试收集曾被 M02 `ABC` 未定义问题阻塞；当前 M02 已修复，剩余失败为 M04/M08。

## 下一步

1. M07 实现 `BaseStrategy` 并适配 `BacktestBarContext`；确保 `created_ts_ms` 等于
   `context.now_ts_ms`。
2. M08 实现 `RiskGateProtocol` 适配器，补齐组合级规则并回报缺口。
3. M03/M04/M05 提供按收盘时点索引的特征提供器，禁止未来函数。
4. M02 提供已确认 K 线装载器和资金费率历史。
5. M11/M12 接入 `BacktestResult.as_record()`、报告文件写入和任务状态。

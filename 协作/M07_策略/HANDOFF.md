# M07 HANDOFF

## 状态

M07 策略框架与 S1/S2/S3 第一版已完成，范围限定在 `src/dquant/strategies/` 与本模块单元测试。
策略只消费公共契约并产生 `Signal`，不访问交易所、不写数据库、不直接下单。

## 开始工作前确认

- 已阅读：`项目执行计划书.md`、`AI模块开发分工与整合方案.md`、`协作/00_总控/接口契约.md`、`协作/00_总控/总进度.md`、`协作/M01_基础设施/HANDOFF.md`。
- 当前 M07 目标：实现可插拔策略接口、注册器、模板、S1/S2/S3，以及供回测/实时调度复用的策略运行入口。
- 允许修改目录：`src/dquant/strategies/`、`tests/unit/test_strategies.py`、`协作/M07_策略/`。
- 禁止修改目录：`src/dquant/contracts/`、总控契约与总进度、其他模块业务目录、配置和项目依赖文件。
- 调查时未发现 M03、M04、M05 的 HANDOFF 文件；因此本模块通过已冻结的公共 Pydantic 模型构造测试 fixture，未复制这些模块的业务实现。

## 已完成内容

1. `base.py`
   - `BaseStrategy`：统一 `accepts`、`on_bar`、`on_fill` 接口。
   - `StrategyContext`：承载选币候选、背离、已确认触发 K 线、ATR、MA200、BTC 趋势和可选持仓状态。
   - 输入校验：拒绝空 K 线、未确认 K 线、乱序时间、跨币种背离/持仓、无效 ATR 和未来时间。
2. `registry.py`
   - `StrategyRegistry`、`@register_strategy`、`create_strategy`、`list_strategies`。
   - 重复注册和未知策略有明确异常。
3. `s1.py`
   - A 池上涨趋势底背离做多、B 池下跌趋势顶背离做空。
   - 结构突破触发；P2 极值外 `0.5 × ATR` 止损。
   - 标准模式 `1.5R` 平半仓；不拆单模式整体 `2R` 止盈。
   - 反向背离平仓、30 根 K 线时间止损、日线破 MA200 且浮盈时移动保本止损。
4. `s2.py`
   - C/D 池、RANGE 状态校验。
   - 独立 4H 最近 50 根区间、边界缓冲、结构突破。
   - 区间外 `1 × ATR` 止损，中轴和对侧边界各半仓止盈。
5. `s3.py`
   - E 池逆势交易，默认 `enabled=False`。
   - 背离评分至少 70，柱高度和柱面积必须同时背离。
   - BTC 反向强趋势过滤；需要 MA200 目标。
   - MA200 与 `2R` 各半仓止盈，策略仓位系数记录在原因中。
6. `template_strategy.py`、`runner.py`
   - 提供不会产生假信号的自定义策略模板。
   - `generate_signals` 按上下文顺序复用同一套策略逻辑，供回测和实时调度调用。
7. `tests/unit/test_strategies.py`
   - 覆盖注册器、正常开仓、空/边界/非法输入、未确认 K 线、未来背离、S3 默认关闭、BTC 过滤、时间止损、重复调用幂等和公共 `Signal` 兼容性。

## 对外暴露接口

```python
from dquant.strategies import (
    BaseStrategy,
    StrategyContext,
    StrategyInputError,
    S1TrendPullbackStrategy,
    S2RangeReversalStrategy,
    S3CounterTrendStrategy,
    TemplateStrategy,
    create_strategy,
    generate_signals,
    list_strategies,
    register_strategy,
)
```

内置策略名称为 `S1`、`S2`、`S3`；`S3` 创建后的默认参数 `enabled=False`。

## 实际检查

```text
py -m pytest -q tests/unit/test_strategies.py
14 passed

ruff check src/dquant/strategies tests/unit/test_strategies.py
All checks passed

ruff format --check src/dquant/strategies tests/unit/test_strategies.py
10 files already formatted
```

## 已知限制与风险

1. M06 回测引擎尚未交付，M07 当前只提供可被回测引擎逐根调用的 `generate_signals`，没有伪造独立回测报告；样本外报告需由 M06 接入后生成。
2. 选币、指标和背离计算由 M03/M04/M05 提供；M07 不重新实现 ATR、MA200、背离或选币规则，调用方必须传入已计算结果。
3. `Signal` 契约目前没有独立的 `strategy_version`、`position_multiplier` 字段。当前版本将策略版本写入 `reason` 和幂等 `signal_id`，将 S2/S3 仓位系数写入 `reason`，供风险层按 `strategy_name`/参数处理。
4. `StrategyContext` 的 `range_candles` 必须由调用方提供完整的 4H 区间数据；没有该数据时 S2 明确不产生信号。
5. 当前未连接 OKX，也没有任何真实密钥、账户信息或外部网络调用。

## 公共接口变更申请

建议由 M00 总控后续评估在 `Signal` 增加以下可选字段：

- `strategy_version: str`：让下游审计、回测和数据库直接记录版本，而不必解析 `reason`。
- `position_multiplier: Decimal`：显式传递 S2 的 `0.5` 和 S3 的 `0.3` 仓位系数，避免风控层依赖策略名称或自由文本。

原因：计划书要求策略信号包含策略版本，并要求 S2/S3 使用不同仓位比例；当前公共模型只包含 `strategy_name`，无法以类型安全方式表达这两个信息。

影响模块：M08 风控、M06 回测、M09 OMS、M10 AI 审计、M11 API/通知、数据库 `strategy_signals` 记录。

兼容方案：在公共字段批准前继续使用现有 `strategy_name`、结构化策略参数和 `reason`；新增字段应设为可选并保留现有调用方兼容。

## 交付给后续模块

- M05：将 `ScreenerCandidate` 和 `DivergenceSignal` 组合成 A～E 候选后，可直接传给对应策略的 `accepts`/`on_bar`。
- M06：逐根构造 `StrategyContext`，调用 `generate_signals`，不要复制 S1/S2/S3 逻辑。
- M08：按 `strategy_name` 与策略参数处理仓位系数；待总控批准公共字段后迁移到显式字段。
- M09：只消费经过风控审核的 `Signal` 转换为 `OrderRequest`，M07 不负责下单。

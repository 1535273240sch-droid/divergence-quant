# M08 HANDOFF

## 状态

已完成 M08 风控计算、开仓审核与紧急开关的第一版实现。实现只使用模拟输入，不连接 OKX，不负责创建订单。

## 完成内容

1. `src/dquant/risk/calculator.py`
   - 使用 `Decimal` 实现最大回撤、按风险比例计算仓位和按 `lot_sz` 向下取整。
   - 使用公共 `Instrument` 的 `ct_val`、`lot_sz`、`min_sz`。
   - 小资金仓位不足以拆成两半时返回 `single_tp_mode=True`；低于 `min_sz` 时返回 `rejected=True`。
   - 保留 `position_size` 兼容入口，但根目录旧原型文件未修改，避免越过本模块文件边界。
2. `src/dquant/risk/manager.py`
   - `RiskConfig`：小资金档默认单笔风险 1.5%，标准档默认 1.0%。
   - `RiskRequest`：接收公共 `Signal`、`Instrument`、`Position` 和市场风险上下文。
   - `RiskManager.evaluate`：审核止损、最大持仓数、同方向持仓数、同币种重复持仓、总名义杠杆、日/周亏损、最大回撤、BTC 过滤、BTC 急涨急跌窗口、周末降仓、资金费率和 AI `VETO/HALF_SIZE`。
   - 非开仓信号返回 `MANAGEMENT_ALLOWED`，不被停止开仓状态阻塞。
   - 可兼容接收 M06 `RiskRequest` 风格对象；若外部请求没有 `instrument`，从构造器传入的交易对映射读取。
3. `src/dquant/risk/killswitch.py`
   - 分离停止开仓、全部平仓请求和最大回撤熔断状态。
   - 熔断只能人工解除；解除熔断不会自动清除人工停止开仓状态。
4. `tests/unit/test_risk.py`
   - 覆盖正常、空输入、边界、非法输入、资金费率、BTC 过滤、急涨急跌、周末、AI 否决/减仓、持仓限制、重复调用和紧急开关。

## 公共接口

```python
from dquant.risk import (
    KillSwitch,
    MarketRiskContext,
    PositionSizing,
    RiskConfig,
    RiskManager,
    RiskRequest,
    calculate_position_size,
    max_drawdown,
)
```

`RiskManager.evaluate` 的返回值是 `dquant.contracts.RiskDecision`。风控不返回 `OrderRequest`，也不调用交易所。

## 目标模块消费关系

- M06 回测可将自己的请求对象传给 `RiskManager.evaluate`，或使用 M08 的 `RiskRequest`。
- M09 消费 `RiskDecision.qty` 和审核结果；平仓、减仓、移动止损信号应继续执行。
- M10 的 `AIReview` 可通过 `MarketRiskContext.ai_review` 提供 `VETO` 或 `HALF_SIZE`。
- M11/M12 可读取 `KillSwitch.state` 对应的停止开仓、全部平仓和熔断状态。

## 实际检查

本轮最后一次已执行的模块测试结果：

```text
py -m pytest -q tests/unit/test_risk.py
11 passed, 1 failed
```

失败原因是测试中的 AI `HALF_SIZE` 断言使用了 1,000 USDT 权益，但默认最小下单步长为 1 张，半仓后的 1.5 张会按规则向下取整为 1 张；随后已将该测试输入调整为 10,000 USDT。根据本轮指令，调整后未重复执行检查。

本轮 `ruff check` 已发现并已修正 M08 文件中的导入顺序、未使用导入和行宽问题；格式检查同时报告了其他 AI 已修改的测试文件存在既有未格式化内容，本模块未修改这些文件。

## 已知限制

1. 根目录 `risk_calculator.py` 没有修改，因此旧测试仍由旧原型直接通过；正式模块提供了 Decimal 版本入口。
2. 当前 `src/dquant/settings.py` 的公共 `RiskSettings` 尚未包含 BTC 熔断窗口、周末系数、资金费率阈值等全部字段。本模块通过 `RiskConfig` 提供默认值，并支持从已有字段读取；没有修改 M01 的设置文件。
3. `RiskDecision` 契约没有 `single_tp_mode`、风控事件级别和结构化拒绝码字段。目前将 `single_tp_mode` 放入 `detail`，将规则编码放入 `rule`。

## 公共接口变更申请

建议由 M00 后续评估在保持向后兼容的前提下，为 `RiskDecision` 增加：

- `single_tp_mode: bool = False`：让 M09 无需解析 `detail` 即可选择整体止盈流程。
- `reason_code` 或枚举化 `rule`：让 M09、M11、M12 稳定展示和记录拒绝原因。
- 可选 `risk_events` 或独立风控事件模型：记录 BTC 熔断、日/周亏损和最大回撤触发的动作。

兼容方案：新增字段均提供默认值，旧消费者继续按现有 `approved`、`qty`、`rule`、`detail` 使用；在契约变更获批前，不修改 `src/dquant/contracts/`。

## 未完成项

- 尚未接入数据库 `risk_decisions` / `risk_events` 的 Repository；数据库表由 M01 提供，写入责任需由总控在后续模块整合时确定。
- BTC 急跌/急涨锁定状态当前保存在进程内 `RiskManager`，部署恢复时应由 M09/M13 持久化或重新从风险事件恢复。
- 真实 OKX 模拟盘联调、邮件告警和 Web 紧急开关尚未实现，分别属于 M02/M09、M11 和 M12。

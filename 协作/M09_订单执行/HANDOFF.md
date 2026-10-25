# M09 HANDOFF

## 状态

已完成 M09 的执行核心第一版：订单状态机、clOrdId 幂等、模拟盘 OMS、超时补单、部分成交保护、对账恢复、半自动确认令牌和持仓保护性动作。

本交付只修改 M09 负责的 `src/dquant/execution/`、M09 集成测试和本协作目录；没有连接 OKX 实盘，也没有修改公共契约、总配置、数据库模型或其他模块业务目录。

## 当前模块目标与边界

- 对应计划书任务：T7.1～T7.4，模块方案第 2 节 M09。
- 允许修改目录：
  - `src/dquant/execution/`
  - `tests/integration/test_execution_demo.py`
  - `tests/integration/test_reconcile.py`
  - `协作/M09_订单执行/`
- 禁止修改且本次未修改：`src/dquant/contracts/`、`src/dquant/adapters/`、`src/dquant/risk/`、`src/dquant/db/`、`config/config.yaml`、`pyproject.toml`、`web/` 和总控文档。
- 依赖：M01 的公共模型/数据库边界，M02 的交易所交易适配器，M08 的 `RiskDecision`。
- 交付消费者：M11 API/确认接口、M13 部署恢复流程、M06 回测/一致性验收，以及后续总控集成测试。

## 完成内容

1. `src/dquant/execution/state.py`
   - `OrderState` 合法迁移表。
   - 终态保护和重复状态回调幂等。
   - `build_client_order_id()`，生成可重复的策略前缀、UTC 毫秒时间戳和序号组合。
2. `src/dquant/execution/ports.py`
   - `ExchangeGateway` 交易端口。
   - `ProtectionRequest`、`RemoteOrder`、`BalanceSnapshot`。
   - 真实 OKX 请求仍由 M02 适配器实现；测试使用明确的 Fake 网关。
3. `src/dquant/execution/store.py`
   - `ExecutionStore` 存储端口。
   - `StoredOrder` 和只用于本地演练/测试的 `InMemoryExecutionStore`。
4. `src/dquant/execution/oms.py`
   - `OMS.submit()` 的 clOrdId 幂等、重复请求冲突检测和默认实盘禁用。
   - `Signal + RiskDecision + Instrument -> OrderRequest` 的数量/价格精度处理。
   - 限价超时撤单；当前价在 `max_market_fallback_slippage` 内才允许沿用原 clOrdId 改市价补单。
   - 成交回报幂等、部分成交/完全成交状态更新，以及增量保护单。
   - 保护单创建失败时禁止新开仓，但不吞掉错误。
   - 启动恢复锁、账户持仓模式和杠杆配置调用边界。
5. `src/dquant/execution/reconcile.py`
   - 交易所挂单、订单历史、持仓和余额读取。
   - 远端订单接管、本地订单状态修正、未解决差异报告。
   - 只有 `safe_to_resume=True` 才能解除 OMS 启动开仓锁。
6. `src/dquant/execution/confirm.py`
   - HMAC 签名、短期有效、只存哈希的一次性确认令牌。
   - 过期、重复点击、放弃、提交失败和确认时价格偏离检查。
7. `src/dquant/execution/management.py`
   - 4H 反向背离保护性全平。
   - 日线反向突破 MA200 且浮盈时，把止损移到带缓冲的保本价。
8. `src/dquant/execution/__init__.py`
   - 汇总导出 M09 对外使用的 OMS、确认、对账相关类型和错误。

## 对外接口

主要入口：

```python
from dquant.execution import (
    ExecutionConfig,
    InMemoryExecutionStore,
    OMS,
    ConfirmationService,
    build_position_actions,
)
from dquant.execution.reconcile import Reconciler
```

OMS 的真实依赖是 `ExchangeGateway` Protocol；M02 适配器需要结构化实现 `place_order`、`cancel_order`、`place_protection_order`、`fetch_open_orders`、`fetch_order`、`fetch_positions`、`fetch_balance`、`set_leverage` 和 `set_position_mode`。

## 测试与检查

M09 限定测试：

```text
py -m pytest -q tests/integration/test_execution_demo.py tests/integration/test_reconcile.py
11 passed
```

M09 静态检查：

```text
ruff check src/dquant/execution tests/integration/test_execution_demo.py tests/integration/test_reconcile.py
All checks passed

ruff format --check src/dquant/execution tests/integration/test_execution_demo.py tests/integration/test_reconcile.py
11 files already formatted
```

全量测试当前结果：`431 passed, 9 failed`。失败集中在其他模块的背离分段/校准、缺失的 `scripts/export_samples.py` 和 `scripts/calibrate_divergence.py`、以及 M08 的 AI 半仓数量断言；没有 M09 测试失败，未修改这些模块以遵守边界。

`py -m mypy ...` 未执行成功：当前环境没有安装 `mypy`（`No module named mypy`）。本次没有修改 `pyproject.toml` 或安装依赖。

## 已知限制与下一步

1. 当前 M02 尚未交付生产 OKX 交易适配器，因此本模块没有真实下单能力；所有测试网关都是 Mock，不含 API Key，不连接实盘。
2. 当前 M09 提供的是 `InMemoryExecutionStore`。M01 已有 `OrderModel` 等 ORM 模型，但 M09 尚未越界修改 M01 文件，也未新增 SQLAlchemy Repository；接入数据库时应实现 `ExecutionStore`，并把订单/成交/对账状态写入现有表。
3. `ProtectionRequest` 是 M09 与 M02 之间的适配器端口，用于表达部分成交后的交易所端保护单；公共 `OrderRequest` 当前没有独立的条件保护单类型。
4. `Reconciler` 需要 M02 提供按 `clOrdId` 查询历史订单的 `fetch_order`，否则挂单从远端消失时只能报告 `unresolved_orders`，不会擅自把它标记成已成交或已取消。
5. `Position.upl` 当前被视为已经按现有公共模型计算后的净浮盈；手续费/资金费字段尚未进入公共模型，因此移动保本判断不能在 M09 内重复计算。
6. `ExecutionConfig` 只承载 M09 最小安全参数。`max_market_fallback_slippage` 和实盘解锁状态正式接入时应由 M01 配置层统一注入，不应再复制到其他模块。

## 公共接口变更申请

本次没有修改公共契约文件，但请总控在整合 M02 时评审以下兼容性申请：

- 需要 M02 的适配器提供 `place_protection_order(ProtectionRequest)`，把保护单映射为 OKX 的附带止盈止损或交易所端 reduce-only 条件单。
- 需要 M02 的适配器提供 `fetch_order(clOrdId)`，用于启动恢复时确认远端已成交/已撤单状态。
- 原因：仅靠当前 `OrderRequest` 和“当前挂单列表”无法安全表达部分成交保护与挂单消失后的最终状态。
- 影响模块：M02 交易所适配器、M09 OMS/对账、M13 恢复流程；M11 只消费 M09 的结果，不需要改变自己的 API 字段。
- 兼容方案：M02 可以在 `adapters/okx.py` 内部把这两个端口映射到 OKX 原生 `attachAlgoOrds`、条件单和订单查询接口，保持 `src/dquant/contracts/` 不变。

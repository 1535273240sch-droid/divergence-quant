# M02 HANDOFF（交易所适配与行情数据）

## 状态

已完成 M02 第一版：`ExchangeAdapter` 抽象、OKX 模拟盘适配器（行情 + 账户查询）、
令牌桶限流、指数退避重试、统一错误封装、历史补数/增量更新/缺口检测修复、日线
北京时间对齐、Redis K 线缓存边界、Binance 归档重聚合，以及两个补数脚本。

## 本次完成

1. `src/dquant/adapters/`
   - `base.py`：`ExchangeAdapter` 抽象接口（计划书 §5.1.1 全部方法，另补充
     `fetch_history_candles` / `fetch_server_time_ms` / `sync_time`，见"接口说明"）。
   - `okx.py`：`OkxAdapter`（demo 强制）、`OkxTransport` 传输抽象、
     `UrllibTransport`（纯标准库实现，含 OKX 签名、`x-simulated-trading: 1` 头、
     HTTP 错误分类）、OKX 响应解析（instruments/tickers/candles/balance/positions/
     open orders/funding rate/time）、`sync_time` 时间同步与偏差告警。
   - `ratelimit.py`：令牌桶限流器（时钟/睡眠可注入）。
   - `retry.py`：指数退避重试（只重试 `retriable=True` 的 `ExchangeError`）。
   - `errors.py`：`ExchangeError`、`classify_okx_code`（50011/50013 可重试）、
     HTTP 状态分类（429/5xx 可重试）。
   - `models.py`：模块内快照模型 `Balance`/`CurrencyBalance`/`FundingRate`/
     `OpenOrderSnapshot`（不在公共契约清单中，见"公共接口变更申请"）。
2. `src/dquant/data/`
   - `candles.py`：周期毫秒表、`validate_bar_alignment`（1D 强制北京时间 00:00，
     即 UTC 毫秒对 86400000 取模 = 57_600_000；1H/4H 按 UTC 边界）、
     `detect_missing_candles`、`detect_price_anomalies`（默认单根涨跌 > 50% 标记）、
     `beijing_date_to_ms`。
   - `store.py`：K 线幂等 upsert（主键 `inst_id+bar+ts_ms`，PostgreSQL 与 SQLite
     共用一套去重与语句构造逻辑）、区间/最新时间戳/收盘价读取。写入前强制对齐校验。
   - `collector.py`：`backfill_history`（分页补数）、`update_incremental`
     （增量 + 未收盘覆盖）、`repair_missing_windows`（缺口定向补数）、
     `load_or_ensure_history_size`（指标预热兜底）、`BackfillStats` 统计与
     `BackfillIncomplete` 显式失败、`AsyncAdapterRunner`（异步适配器 → 同步协议桥）。
   - `cache.py`：`CandleCache` 协议 + 内存实现 + Redis 实现（每对最近 500 根，
     键 `dquant:candles:{inst_id}:{bar}`）。
   - `binance_archive.py`：Binance 公开月度归档 ZIP 解析（兼容毫秒/微秒时间戳）、
     1h → UTC+8 日线重聚合（与 OKX `bar=1D` 同一验证规则）、注入式下载。
3. `scripts/`
   - `backfill.py`：OKX 补数脚本（只调用公开接口、无需密钥、强制 demo、
     `--dry-run` 支持、写入库必须显式 `--database-url`）。
   - `backfill_binance_archive.py`：Binance 归档脚本（输出 JSONL，可选 `--write-db`）。
4. 测试与 fixture
   - `tests/unit/test_m02_*.py` 7 个文件、76 个用例；`tests/unit/m02_helpers.py`。
   - `tests/fixtures/m02/okx_*.json`：OKX 录制响应样本（instruments/tickers/candles）。

## 修改文件

- 新增：`src/dquant/adapters/{__init__,base,okx,errors,ratelimit,retry,models}.py`
- 新增：`src/dquant/data/{__init__,candles,store,collector,cache,binance_archive}.py`
- 新增：`scripts/backfill.py`、`scripts/backfill_binance_archive.py`
- 新增：`tests/unit/test_m02_*.py`（7 个）、`tests/unit/m02_helpers.py`、
  `tests/fixtures/m02/okx_instruments.json`、`okx_candles.json`、`okx_tickers.json`
- 覆写（M00 预留给 M02 的占位文件）：`src/dquant/adapters/__init__.py`、
  `src/dquant/data/__init__.py`
- 未修改：`contracts/`、`db/`、`settings.py`、`config/`、`pyproject.toml`、
  其他模块目录、`risk_calculator.py`、`web/`

## 暴露的公共接口

```python
from dquant.adapters import (
    ExchangeAdapter, ExchangeError, OkxAdapter, OkxTransport, UrllibTransport,
    OkxCredentials, TokenBucketLimiter, RetryPolicy,
    Balance, CurrencyBalance, FundingRate, OpenOrderSnapshot,
)
from dquant.data import (
    backfill_history, update_incremental, repair_missing_windows,
    load_or_ensure_history_size, AsyncAdapterRunner, SyncCandleSource,
    upsert_candles, load_candles_between, load_latest_candle_ts,
    validate_bar_alignment, detect_missing_candles, detect_price_anomalies,
    InMemoryCandleCache, RedisCandleCache,
    collect_symbol_daily, aggregate_daily_shanghai,
)
```

关键约定（M03/M05/M06/M08/M09 消费方必读）：

- `fetch_candles` / `fetch_history_candles` 返回**升序** `Candle` 列表；
  `Candle.volume` 是 OKX SWAP 的**张数**（vol），`quote_volume` 是 USDT 计价量
  （volCcyQuote）；`Ticker.quote_volume_24h` = `volCcy24h × last`。
- `fetch_candles(since_ms)` 语义是"since 之后最新的 limit 根"；更深的缺口必须用
  `fetch_history_candles` 分页（`older_than_ms` 对应 OKX `after`，`newer_than_ms`
  对应 `before`）。
- `OkxAdapter` 构造时强制 `mode="demo"`，其余模式直接抛 `ExchangeError`；
  `place_order`/`cancel_order`/`set_leverage`/`subscribe_*` 属于 M09 执行域，
  当前显式抛 `NotImplementedError`（不是静默成功）。
- 私有接口（balance/positions/orders-pending）在未配置凭证时抛
  `ExchangeError("NO_CREDENTIALS")`；凭证只允许通过 `OkxCredentials` 从环境变量
  构造，代码与测试中没有任何密钥。
- 存储层是**同步** SQLAlchemy 会话（见"已知边界"第 1 条），不做 commit，
  由调用方控制事务；写入强制周期对齐校验，1D 非 UTC 16:00 的时间戳会抛
  `DataQualityError`。
- 补数/增量更新全部幂等：重复执行不产生重复行；交易所本身缺失的 K 线不会被
  静默填补，而是出现在 `BackfillStats.missing_windows` 中。

## 如何运行测试

```bash
py -m pytest -q tests/unit/test_m02_ratelimit.py tests/unit/test_m02_okx_adapter.py \
  tests/unit/test_m02_candles_quality.py tests/unit/test_m02_store.py \
  tests/unit/test_m02_collector.py tests/unit/test_m02_cache.py \
  tests/unit/test_m02_binance_archive.py
```

## 测试实际结果

在项目根目录实际执行（2026-10-24）：

```text
ruff check [M02 全部目录与文件]      # All checks passed!
ruff format --check [同上]           # 24 files already formatted
py -m pytest -q [M02 7 个测试文件]   # 76 passed
py -m pytest -q [M02 + M00/M01 基线] # 85 passed
py scripts/backfill.py --dry-run     # rc=0，正常打印补数计划
py scripts/backfill_binance_archive.py --dry-run   # rc=0，正常打印归档 URL 清单
py scripts/backfill.py 缺 --database-url           # rc=2，明确报错拒绝执行
```

全量 `py -m pytest -q` 中 `test_divergence.py`（M04）与 `test_risk.py`（M08）
存在 9 个失败，均为其他模块自身的断言失败（段切分数量、风控 qty 计算），与 M02
代码无关（堆栈中不含 M02 模块），按边界规则未代为修改。

## 公共接口变更申请

1. **申请将 `Balance`、`FundingRate` 提升为公共契约模型**
   - 需要什么：在 `contracts/models.py` 增加 `Balance`（ts_ms、total_equity_usd、
     按 ccy 的明细）与 `FundingRate`（inst_id、funding_rate、funding_time_ms、
     next_funding_time_ms）。
   - 为什么：M08 的资金费率过滤、M09 的对账、M11 的权益展示都需要这两类数据；
     目前它们定义在 `dquant/adapters/models.py`（模块内），跨模块直接 import 会
     形成 M08/M09 → adapters 的反向依赖。
   - 影响模块：M08、M09、M11（消费）；M02（生产）。
   - 兼容方案：提升后 `dquant.adapters.models` 中的同名类改为从 contracts re-export，
     现有 M02 测试与调用方不破坏。
   - 当前处理：在提升前，其他模块**不应** import `dquant.adapters.models`；
     需要资金费率/权益数据的模块请先走契约变更流程。
2. **申请在 `ExchangeSettings`（settings.py）中增加限流与重试参数**
   - 需要什么：`rate_per_sec`、`rate_capacity`、`max_attempts`、`base_delay_sec`
     四个字段（含默认值）。
   - 为什么：当前适配器用代码默认值（5 req/s、3 次尝试），无法按部署环境调整。
   - 影响模块：仅 M02 消费；字段新增向后兼容。
   - 当前处理：未改 `settings.py`（属 M01 边界），待总控批准后由 M01/M00 落地。

## 已知边界与未完成内容

1. **存储层为同步会话**：补数/增量更新面向脚本与批处理任务。当前项目依赖中没有
   异步 SQLite 驱动，异步路径无法在本模块完成真实集成测试，因此长驻服务的异步
   会话接入（M05 调度器/M09 执行器内）留给集成批次，届时复用 `build_candle_upsert`
   的语句构造逻辑，不复制业务去重规则。
2. **WebSocket 订阅未实现**：`subscribe_candles`/`subscribe_orders` 需要第三方
   WebSocket 库（不在当前依赖中），抽象方法由 M09/M13 集成时实现；本模块用
   `NotImplementedError` 明确表达边界。
3. **150 币三周期真实补数未执行**：T1.5 的验收（150 币 × 1H/4H/1D 补数完成、
   主流币 2019 至今无缺口）需要真实网络与数据库环境，由总控在模拟盘环境运行
   `scripts/backfill.py` 验收；本模块已用假采集源对全部编排逻辑做了确定性测试。
4. **TimescaleDB hypertable 转换未做**：与 M01 的边界一致，放到部署迁移（M13）。
5. **mypy 未安装**（与 M00/M01 相同的环境限制），所有代码已按
   `disallow_untyped_defs` 标准补全类型注解，但不能报告类型检查通过。
6. **OKX 4H K 线对齐假设**：代码假设 OKX `bar=4H` 按 UTC 0/4/8/12/16/20 对齐，
   已在 `validate_bar_alignment` 中强制；若与交易所实际返回不符，以真实数据为准
   修正该规则（首次真实补数时核对）。

## 依赖下一模块做什么

- **M03**：用 `load_instruments`（币池元数据）+ `load_candles_between`/缓存读取
  已收盘 K 线计算指标与趋势；只消费 `confirmed=True` 的数据。
- **M05（调度器）**：每根 4H 收盘后调用 `update_incremental`（建议 lookback ≥
  2 个周期）再触发扫描；`BackfillStats.missing_windows` 非空时告警。
- **M09**：在 `OkxAdapter` 上补齐 `place_order`/`cancel_order`/`set_leverage`/
  `subscribe_*`（复用 `_call` 的限流重试编排与 `OkxCredentials`），并落实
  实盘安全门禁；如需扩展传输层请保持 `OkxTransport` 抽象不变。
- **M13**：真实数据库环境下执行 `alembic` 迁移 + hypertable 转换后，
  用真实网络跑一次 `scripts/backfill.py` 完成 T1.5 验收。

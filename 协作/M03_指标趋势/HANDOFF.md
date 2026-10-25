# M03 HANDOFF

## 状态

已完成 M03 第一版：纯函数指标库、币池初筛、SMA200/EMA200 双轨趋势分类与防抖。

对应计划书任务：**T2.1、T2.2、T2.3**（§5.3、§5.4、§5.5）。

## 本次完成（逐项）

### 1. `src/dquant/core/indicators.py` —— 纯函数指标库

- 实现 `sma`、`ema`、`macd`、`rsi`、`atr`、`adx`，全部为 numpy 数组输入/输出，无副作用。
- 预热期统一用 `NaN` 填充；空输入返回空数组；输入长度不足周期时返回全 `NaN`。
- 非法输入显式报错：非正周期、OHLC 长度不一致、`high < low`、`NaN`/`inf`、二维输入、`macd` 的 `fast >= slow`。
- 因果性测试：截断未来数据后，历史输出逐值不变（EMA/RSI/ATR/ADX/MACD 全部覆盖）。
- 与 TA-Lib 0.8.2 逐值对照，实测最大误差约 `1e-13`，远小于要求的 `1e-6`。

**与 TA-Lib 对齐的两处关键口径（后续模块不要自行改写）：**

1. **MACD**：快线 EMA 的种子不是从第 0 根开始，而是右对齐到慢线种子窗口末端
   （`close[slow-fast : slow]` 的 SMA）；信号线用前 `signal` 个 DIF 的简单平均作为种子；
   TA-Lib 的 MACD 从索引 `slow + signal - 2`（默认 33）才开始输出，本模块保持一致。
2. **ADX**：TA-Lib 先用 `n-1` 根原始 TR/DM 建立初值，再对后续 `n` 根做 Wilder 平滑并累加
   DX，首个有效值位于索引 `2n-1`。按教科书“n 根初始累加”实现会与 TA-Lib 产生可见偏差，
   因此本模块按 TA-Lib 的实际代码路径实现。

### 2. `src/dquant/core/regime.py` —— MA200 趋势分类器

- `RegimeConfig`：默认值对齐计划书 §5.5/§8（MA200、斜率 20 根/1%、窗口 20、上下占比
  0.8/0.2、穿越上限 3、ADX 14/20、确认 3 根），所有参数可配置并有显式校验。
- `classify_market_state`：单点规则入口，便于诊断、回测与单测；`adx=None` 时跳过 ADX 辅助条件。
- `debounce_states`：通用滞后确认；首个原始状态直接生效（此前没有旧状态可切换），
  之后新状态必须连续 `confirm_bars` 根一致才切换，同时输出每根 K 线的 `bars_in_state`。
- `regime_series`：返回从第一根可计算 K 线开始的逐根 `RegimeResult`，供 M06 回测使用。
- `classify_regime`：返回最新一根已收盘 K 线的 `RegimeResult`。
- `classify_regime_all`：按 `RegimeConfig.ma_types` 同时输出 SMA200 与 EMA200 两套结果
  （默认两套都计算，`primary_ma` 默认 `SMA`）。
- `InsufficientCandlesError(required, actual)`：数据不足时抛出，带明确数量信息。

**重要边界：**

- 默认参数下需要 **220 根日线**才能输出完整 `RegimeResult`：MA200 本身只需 200 根，
  但斜率（20 根回看）、`above_ratio`（20 根窗口）、`cross_count`（20 根窗口 + 前一根比较）
  需要到索引 219。`min_history_bars=200` 只保证 MA200 可算，不能保证 `RegimeResult` 完整。
- 严格只接受 `Candle.confirmed is True` 的 K 线；未收盘 K 线、`inst_id`/`bar` 不一致、
  时间戳乱序或重复都会显式报错。**本模块不做隐式过滤**，调用方必须先过滤未收盘 K 线，
  避免静默使用未收盘数据。
- 任一时刻的分类只依赖当前及历史 K 线；追加未来 K 线不会改变历史结果（已用截断测试验证）。
- 实测性能：单币 5000 根日线 `classify_regime` 约 **0.066 秒**；300 币约 20 秒（仅趋势部分）。

### 3. `src/dquant/core/universe.py` —— 币池初筛

- `UniverseConfig`：默认值与计划书 §5.4/§8 一致（USDT、2000 万 USDT、200 根、Top 150、
  黑白名单、排除稳定币）。
- `screen_universe(instruments, tickers, history_bars, config=...)`：
  - 规则顺序：黑名单 → `state != live` → 白名单 → 计价币 → 稳定币基础币 → 24h 成交额 →
    历史根数 → Top N。
  - 黑名单优先于白名单；白名单强制纳入，可绕过成交额/历史长度/Top N/稳定币规则，
    **但不能绕过 `not_live`**（安全优先）。
  - 白名单不占用 Top N 名额；白名单中不存在于 `instruments` 的条目会得到
    `WHITELIST_NOT_LISTED` 记录。
  - 结果完全确定性：成交额降序、同额按 `inst_id` 升序；排除记录按 `inst_id` 排序。
  - 每个输入交易对恰好产生一条入选或排除记录，`UniverseReason` 为稳定枚举。
- `history_bars` 由调用方（M02/M05）提供“已确认日线根数”；本模块不访问数据库。

### 4. 测试

- `tests/unit/test_indicators.py`：40 项（TA-Lib 对照、预热期、因果性、非法输入、纯函数）。
- `tests/unit/test_regime.py`：54 项（三态判定、规则分支、防抖、序列对齐、因果性、
  双轨输出、配置与数据校验）。
- `tests/unit/test_universe.py`：32 项（全部筛选规则、黑白名单优先级、Top N、确定性、
  完整性、非法输入）。

## 修改文件

**新增：**

- `src/dquant/core/indicators.py`
- `src/dquant/core/regime.py`
- `src/dquant/core/universe.py`
- `tests/unit/test_indicators.py`
- `tests/unit/test_regime.py`
- `tests/unit/test_universe.py`
- `协作/M03_指标趋势/HANDOFF.md`

**未修改：** `contracts/`、`settings.py`、`config/`、`pyproject.toml`、其他模块目录与测试。
（`src/dquant/core/screener.py`、`src/dquant/core/pivots.py` 由 M05/M04 并行创建，未触碰。）

## 公共接口（供 M04/M05/M06/M08 使用）

```python
from dquant.contracts import Candle, CandleBar, RegimeResult, RegimeState
from dquant.core.indicators import adx, atr, ema, macd, rsi, sma
from dquant.core.regime import (
    InsufficientCandlesError,
    RegimeConfig,
    classify_market_state,
    classify_regime,
    classify_regime_all,
    debounce_states,
    regime_series,
)
from dquant.core.universe import (
    UniverseConfig,
    UniverseReason,
    UniverseResult,
    screen_universe,
)

# 指标：输入收盘价数组，返回等长 float64 数组，预热期为 NaN
ma200 = sma(closes, 200)
dif, dea, hist = macd(closes, 12, 26, 9)  # 从索引 33 开始有值

# 趋势：严格传入 confirmed=True、按 ts_ms 升序的 K 线
config = RegimeConfig()                     # 默认 SMA + EMA 双轨
latest = classify_regime("BTC-USDT-SWAP", CandleBar.ONE_DAY, candles)
both = classify_regime_all("BTC-USDT-SWAP", CandleBar.ONE_DAY, candles)
series = regime_series("BTC-USDT-SWAP", CandleBar.ONE_DAY, candles)  # 逐根，供回测

# 币池：tickers 支持 Sequence[Ticker] 或 Mapping[str, Ticker]
pool = screen_universe(instruments, tickers, history_bars, config=UniverseConfig())
pool.selected_inst_ids
pool.reason_for("BTC-USDT-SWAP")           # UniverseReason | None
```

从 M01 配置映射（示例，不新增依赖）：

```python
cfg = UniverseConfig(
    quote=settings.universe.quote,
    min_quote_volume_24h=Decimal(settings.universe.min_quote_volume_24h),
    min_history_bars=settings.universe.min_history_bars,
    top_n_by_volume=settings.universe.top_n_by_volume,
    whitelist=tuple(settings.universe.whitelist),
    blacklist=tuple(settings.universe.blacklist),
)
```

## 如何运行测试

在项目根目录：

```bash
py -m pytest tests/unit/test_indicators.py tests/unit/test_regime.py tests/unit/test_universe.py -q
ruff check src/dquant/core/indicators.py src/dquant/core/regime.py src/dquant/core/universe.py tests/unit/test_indicators.py tests/unit/test_regime.py tests/unit/test_universe.py
ruff format --check src/dquant/core/indicators.py src/dquant/core/regime.py src/dquant/core/universe.py tests/unit/test_indicators.py tests/unit/test_regime.py tests/unit/test_universe.py
```

## 实际检查结果（本机）

```text
py -m pytest tests/unit/test_indicators.py tests/unit/test_regime.py tests/unit/test_universe.py -q
# 126 passed in 2.62s

py -m pytest tests/unit/test_contracts.py tests/unit/test_m01_db.py tests/unit/test_m01_settings.py tests/test_project_layout.py -q
# 12 passed in 2.44s（M00/M01 既有测试回归，未被本模块破坏）

ruff check <上述 6 个文件>          # All checks passed
ruff format --check <上述 6 个文件> # 6 files already formatted
```

说明：

- 本机 Python 3.14.4，已安装 numpy 2.5.1；为执行 TA-Lib 对照测试，已通过
  `py -m pip install "TA-Lib>=0.6.4"` 在本机环境安装 TA-Lib 0.8.2（未修改仓库文件）。
- 缺少 TA-Lib 时，仅 6 个对照测试通过 `pytest.importorskip("talib")` 跳过，
  其余指标测试和全部趋势/币池测试仍会执行。
- `py -m mypy` 未安装，**不能报告类型检查通过**。

## 已知问题与限制

1. **依赖声明缺口（需要 M00 处理）**：`pyproject.toml` 没有声明 `numpy`，但 M03 运行必需。
   干净环境安装项目后导入本模块会失败。已在下方“公共接口/依赖变更申请”中提出。
2. TA-Lib 只用于测试对照，不是运行时依赖；但 `pyproject.toml` 的 dev 依赖也未声明。
3. 用 `numpy` 的 `cumsum` 计算 SMA，极端长序列（> 10 万根）的浮点误差会随累计和增长；
   日线 5000～2 万根范围内实测远小于 `1e-6`。
4. 本机全量 `py -m pytest -q` 当前会被其他模块的在建测试收集错误中断：
   `test_ai_gateway.py`、`test_execution_demo.py`、`test_reconcile.py`、
   `test_ai_fallback.py`、`test_divergence.py`、`test_m02_okx_adapter.py`、
   `test_m02_ratelimit.py`（`NameError: name 'ABC' is not defined`）。
   **这些文件不属于 M03，未做任何修改**；请对应模块修复后再跑全量。
5. 计划书 §5.3 表格中的 `pivots` 按分工归 M04（`core/pivots.py`，T3.1），
   本模块未实现，避免与 M04 重复。
6. 趋势规则的 ADX 分支按 §5.5 原文实现；由于趋势条件本身要求 `|slope| > 1%`，
   “`|slope| < 1%` 且 `ADX < 20`”这一条在逻辑上与“不满足趋势条件”等价，
   实际不会改变分类结果，但保留该判断以忠实表达配置语义。

## 公共接口 / 依赖变更申请

**申请内容**：请 M00 在 `pyproject.toml` 中补充依赖声明。

- `[project].dependencies` 增加 `"numpy>=1.26,<3"`（M03 运行时必需，M04/M05/M06 也会使用）。
- `[project.optional-dependencies].dev` 增加 `"TA-Lib>=0.6.4"`（仅用于指标对照测试；
  不影响生产运行）。

**原因**：M03 指标库以 numpy 数组为输入/输出，计划书 §5.3 明确要求；当前环境能跑是因为
本机已单独安装 numpy，干净环境无法导入。

**影响模块**：M03、M04、M05、M06（指标与背离计算）。

**兼容方案**：只新增依赖，不改变任何既有字段或接口；不涉及 `contracts/` 变更。

## 依赖下一个模块做什么

- **M04 背离检测**：
  - 直接使用 `macd` / `rsi` / `atr` 等指标，不要在 M04 复制指标实现。
  - 注意 MACD 三个输出都从索引 `slow + signal - 2` 开始有值，预热期为 `NaN`。
  - `pivots` 由 M04 自行实现（T3.1）。
- **M05 选币器/调度**：
  - 用 `screen_universe` 做初筛；`history_bars` 需要 M02/M05 统计“已确认日线根数”。
  - 用 `classify_regime_all` 同时落库 SMA200/EMA200 两套 `RegimeResult`，
    按 `RegimeConfig.primary_ma` 选择交易主轨。
  - 调用前先过滤 `confirmed=False` 的 K 线；默认参数下至少需要 220 根日线。
  - `UniverseResult.reason_for` 可直接用于选币运行日志与 Web 展示。
- **M06 回测**：
  - 用 `regime_series` 获取逐根 `RegimeResult`，不要自行实现“回测专用趋势逻辑”；
    序列只包含可计算区间，需按 `ts_ms` 对齐。
- **M08 风控**：只消费 `RegimeState`/`RegimeResult`，不要在前置模块重复趋势判定规则。

## 未完成内容

- 无 TODO、`pass`、空函数或假成功结果。
- 未实现计划书 §5.3 的 `pivots`（按分工归 M04）。
- 未接入数据库、调度器、补数脚本或 Web（不属于 M03 范围）。
- 未实现 SMA/EMA 对比回测报告（T5.4，归 M06）。

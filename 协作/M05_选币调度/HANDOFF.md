# M05 HANDOFF

## 状态

已完成 M05 第一版：选币器纯函数核心（T4.1）与调度/持久化边界（T4.2）。

上游 M03/M04 在本模块开发期间陆续落地（`core/indicators.py`、`regime.py`、`divergence.py` 等），
M05 全程只依赖 `dquant.contracts` 的公共模型（`RegimeResult`、`DivergenceSignal`、`ScreenerCandidate`），
不 import 任何 M03/M04 实现细节，因此上游改动不会破坏本模块。

## 本次完成

1. `src/dquant/core/screener.py`（纯函数，无 DB / 无网络 / 无全局状态）
   - `ScreenerConfig`：Pydantic 配置模型，字段与计划书第 8 章 screener 节一致
     （pools_trade、pools_notify_only、pool_size_multiplier、top_n_per_pool），
     另含 5.7 节评分权重 0.6/0.3/0.1 与归一化上限，全部可配置并做严格校验。
   - `assign_pool`：按"趋势状态 × 背离类型"映射 A～F 池
     （BULL_*/BEAR_* 前缀决定底背离/顶背离，隐藏背离与常规背离同池）。
   - `trend_strength` / `liquidity_strength` / `compute_score`：
     综合评分 = 背离分 × 0.6 + 趋势强度 × 0.3 + 流动性 × 0.1；
     F 池（无背离）按趋势+流动性权重占比重新归一到 0~100。
   - `run_screener`：一次完整扫描——去重趋势快照（同 inst 取最新 ts）、
     忽略 preliminary 早期提示、同 inst 多信号择优（分数 > 确认时间新 > 派生 id 确定性）、
     池内按分数降序 + inst_id 升序排名并截取 Top N、生成 params_hash（配置 SHA-256）。
     相同输入 + 相同配置 + 相同时间戳必然得到相同结果。
   - `derive_divergence_id`：从信号自然键（品种/周期/类型/柱段区间/序号/确认时间）
     派生 32 位十六进制稳定 id。评分不参与身份计算，参数校准后引用不失效。
2. `src/dquant/scheduler/`
   - `persistence.py`：`ScreenerRunStore` 读写 `screener_runs` / `screener_results`。
     save_run 在单事务内"先删同 run_id 旧结果再写入 + merge 运行行"，同一 run_id
     重复保存是替换语义，不产生重复记录或脏数据。run_id 约定：`sched-<close_ts_ms>`（定时，
     确定性）、`manual-<uuid>`（手动）。读写失败抛 `ScreenerStoreError`，回滚失败留告警日志。
     另导出 `run_row_from_record` / `result_rows_from_record` / `record_from_rows` 纯函数。
   - `service.py`：`ScreenerService` 组装"注入的 input_provider → 纯函数选币 → 落库"。
     定时扫描用确定性 run_id（同一收盘重复触发替换同一条记录），手动扫描每次新记录。
     `utc_now_ms` 为默认时钟，可注入以便测试与回放。
   - `runner.py`：`ScreenerScheduler` 按背离周期 K 线收盘轮询触发（默认 4H，UTC 边界对齐）。
     同一收盘只触发一次；失败不推进水位、下轮 poll 自动重试；进程重启后只补跑最新收盘，
     跳过的旧收盘记 WARNING。`run_forever` 提供内置间隔的便捷循环；`run_manual` 透传手动触发。
3. 测试
   - `tests/unit/test_screener.py`：池映射全组合、评分、配置校验、空输入、preliminary 过滤、
     多信号择优、孤儿背离告警、重复快照、非法输入、确定性、契约兼容、divergence_id 稳定性。
   - `tests/unit/test_scheduler.py`：行构建纯函数、run_id 解析、SQLite 真实 SQL 的存取/替换/
     幂等/失败传播、服务落库、4H 边界数学、poll 去重/补跑/重试、run_forever 停止、端到端流水线。

## 修改文件

- 新增 `src/dquant/core/screener.py`
- 重写 `src/dquant/scheduler/__init__.py`（原为 M00 占位空文件）
- 新增 `src/dquant/scheduler/persistence.py`、`src/dquant/scheduler/service.py`、`src/dquant/scheduler/runner.py`
- 新增 `tests/unit/test_screener.py`、`tests/unit/test_scheduler.py`
- 新增本 HANDOFF（`协作/M05_选币调度/HANDOFF.md`）

未触碰：`contracts/`、`settings.py`、`db/`、`config/`、`pyproject.toml`、alembic、
其他模块目录、`risk_calculator.py`、`web/`、`core/__init__.py`（避免与 M03/M04 并行编辑冲突，
消费者请直接 `from dquant.core.screener import ...`）。

## 公共接口（供下游消费）

```python
# 纯函数核心（M07 策略、M06 回测、M10/M11/M12 可复用）
from dquant.core.screener import (
    ScreenerConfig,        # 选币配置（权重、TopN、池清单、归一化上限）
    ScreenerInputs,        # 一次扫描的输入（regimes / divergences / liquidity）
    ScreenerRunRecord,     # 运行结果（含 candidates: tuple[ScreenerCandidate, ...]）
    run_screener,          # 纯函数扫描入口
    assign_pool,           # 池映射
    compute_score,         # 综合评分
    derive_divergence_id,  # 信号自然键 -> 稳定 id
)

# 调度与持久化
from dquant.scheduler import (
    ScreenerRunStore,      # screener_runs / screener_results 读写（含 load_latest_candidates）
    ScreenerService,       # run_scheduled(close_ts_ms) / run_manual()
    ScreenerScheduler,     # poll(now_ms) / run_manual() / run_forever(...)
    ScreenerStoreError,
    utc_now_ms,
)
```

- 输出对象是契约模型 `ScreenerCandidate`，字段与 `contracts/models.py` 完全一致。
- `ScreenerService.input_provider` 是 `Callable[[], Awaitable[ScreenerInputs]]`；
  行情/趋势/背离的真实采集装配（从 M02/M03/M04 的输出取数）属于集成批次，
  由总控或后续装配层提供该回调。

## 如何运行测试

```bash
ruff check src/dquant/core/screener.py src/dquant/scheduler tests/unit/test_screener.py tests/unit/test_scheduler.py
ruff format --check src/dquant/core/screener.py src/dquant/scheduler tests/unit/test_screener.py tests/unit/test_scheduler.py
py -m pytest -q tests/unit/test_screener.py tests/unit/test_scheduler.py
```

## 测试实际结果（2026-10-24，Windows，Python 3.14）

```text
ruff check（上述范围）            # All checks passed
ruff format --check（上述范围）    # 7 files already formatted
py -m pytest -q tests/unit/test_screener.py tests/unit/test_scheduler.py
                                  # 64 passed
加跑 M00/M01 既有测试（contracts、m01_db、m01_settings、project_layout）
                                  # 合计 76 passed
py -m mypy .                      # 未执行：环境未安装 mypy（与 M00/M01 记录一致）
```

性能基准（T4.1 目标：300 交易对全量扫描 ≤ 60 秒）：
纯函数核心对 300 个交易对、225 条背离信号的一次完整扫描耗时约 **10.5 ms**
（产出 6 池 × Top5 共 30 个候选）。纯计算远低于目标，将来整链路耗时由行情采集与 DB 决定。
本模块未做 DB/网络路径的性能验收（依赖真实 PostgreSQL，属集成批次）。

## 设计决策（下游需要知道）

1. **池映射**：底背离 = `BULL_*`，顶背离 = `BEAR_*`；`BULL_HIDDEN`/`BEAR_HIDDEN`
   与对应常规背离同池（A/B/C/D/E）。无背离（或仅有 preliminary 信号）进 F 池。
2. **F 池评分**：无背离时不计背离项，按 (趋势×0.3 + 流动性×0.1) / 0.4 归一到 0~100。
3. **归一化上限默认值**：`trend_dist_cap=0.15`、`trend_slope_cap=0.05`、
   `liquidity_quote_volume_cap=1 亿 USDT`。计划书未规定具体上限，这三个默认值
   需要在回测阶段（T5.x）校准，不要当作结论。
4. **幂等语义**：定时扫描的 run_id 由收盘时间戳决定，同一收盘重复扫描替换同一条
   运行记录（先删后写、单事务）；手动扫描每次都是新记录。
5. **读回是有损的**（计划书第 7 章表结构无这些列，M05 不能改表）：
   - `divergence_kind` 读回为 None（下游需要类型请按 divergence_id 关联背离信号表）；
   - 候选的 `updated_ts_ms` 读回用 run 的 started_at 代替；
   - `warnings` 是运行期诊断，不落库。
6. **调度语义**：收盘边界按 UTC 对齐（4H = 00/04/08/... UTC）；日线边界仍由
   `daily_close_tz=Asia/Shanghai` 的 M02 数据层负责，调度器只按 bar 周期取边界。
   失败自动重试同一收盘；错过的旧收盘只补跑最新一次并告警。

## 公共接口变更申请（请总控裁决，M05 未擅自改动）

1. **AppSettings 缺少 screener 配置节**（影响 M01/M00）：
   计划书第 8 章定义了 `screener:` 节，但 `AppSettings` 没有 `ScreenerSettings`，
   `config.yaml` 也没有该节。M05 现用模块内 `ScreenerConfig`（默认值取自计划书），
   不阻塞运行。建议 M01 在 `settings.py` 增加 `screener` 节并在 `config.yaml` 落默认值，
   字段与 `ScreenerConfig` 一一对应；迁移方式为"新增可选节，缺省时行为不变"。
2. **背离信号身份问题**（影响契约 M00 / M04 / M01）：
   契约 `DivergenceSignal` 没有 id 字段，而 `screener_results.divergence_id` 需要引用；
   `screener_results` 表也没有 `divergence_kind` 列，读回候选时类型信息丢失。
   可选方案（任一即可）：a) 契约给 `DivergenceSignal` 增加可选 `divergence_id`，
   由 M04 落库时生成；b) 未来迁移给 `screener_results` 增加 `divergence_kind` 列。
   M05 目前用自然键派生 id（`derive_divergence_id`），M04 落库后若提供真实 id，
   选币输入装配层应改传真实 id，`derive_divergence_id` 仅作无 id 时的兜底。
3. **`db.session.session_scope` 不是 async context manager**（反馈 M01）：
   它是裸 async 生成器（未加 `@asynccontextmanager`），`async with session_scope(f)` 会
   TypeError。M05 的 store 自管会话生命周期未受影响，但其他模块按直觉使用会踩坑，
   建议 M01 修复（加装饰器或改为 asynccontextmanager 实现）。
4. **开发依赖建议**（M00）：持久化测试用"同步 SQLite 会话 + 最小异步适配器"执行真实 SQL。
   若总控希望直接用异步 SQLite，请把 `aiosqlite` 加入 dev 依赖（pyproject 由 M00 维护）。

## 已知问题 / 观察到的跨模块问题（非 M05 范围，未修改）

- 开发期间其他 AI 落地的 M02/M10 测试文件存在收集错误：
  `tests/unit/test_m02_*.py`、`tests/integration/test_ai_gateway.py` 共 7 个文件
  `NameError: name 'ABC' is not defined`，导致 `py -m pytest -q`（全仓）在收集阶段失败。
  该错误属于 M02/M10 的文件，M05 无权修改，请总控转交对应模块处理。
- 全仓 `ruff check .` 有约 104 个错误、23 个文件未格式化，均位于其他模块新增文件中；
  M05 自己的 7 个文件 ruff 全绿。
- mypy 未安装，类型检查未运行（环境限制，与 M00/M01 记录一致）。

## 未完成的内容（不模糊）

- 没有真实 PostgreSQL / Redis 集成验收：store 的 SQL 逻辑用 SQLite 验证，真实 PG 的
  写入、事务与并发行为留给集成批次（与 M01 的边界一致）。
- 没有接入真实行情/趋势/背离数据源的装配：`ScreenerService.input_provider` 的生产实现
  需要等 M02 数据链路稳定后由集成层接线。
- 没有实现"扫描结果推送通知"：计划书中 pools_notify_only 的消费方是通知层（M11），
  M05 只输出候选与配置，不做通知。
- 未跑 mypy（环境无 mypy）。

## 依赖下一个模块做什么

- 集成/装配层（总控）：实现 `input_provider`——从 M02 取 24h 成交额、从 M03 取最新
  `RegimeResult`（`primary_ma` 那一套）、从 M04 取非 preliminary 的 `DivergenceSignal`；
  并把 `ScreenerService`/`ScreenerScheduler` 挂进常驻进程。
- M07 策略：按 `ScreenerCandidate.pool` 消费 A/B/C/D/E 池；F 池仅展示。
- M11/M12：读 `store.load_latest_candidates()` / `load_latest_run()` 展示选币结果；
  注意读回的 `divergence_kind` 为 None 的限制（见变更申请 2）。
- 回测（M06）：校准 `ScreenerConfig` 的三个归一化上限默认值。

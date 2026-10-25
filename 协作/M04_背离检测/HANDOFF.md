# M04 HANDOFF

## 状态

M04 第一版已完成：枢轴点识别、MACD 柱段背离检测、评分、二次背离、早期提示、
样本导出脚本与参数校准脚本，全部测试通过。

## 完成了什么

1. `src/dquant/core/pivots.py`
   - `find_pivot_lows` / `find_pivot_highs`：经典枢轴法（默认 left=5, right=5）。
   - 确认延迟：`confirmed_index = index + right`，右侧窗口不完整不产生枢轴。
   - 并列极值取最左出现者。
2. `src/dquant/core/divergence.py`
   - `DivergenceConfig`：参数与计划书 §8 `divergence` 段一致（含 macd、segment、
     gap、area_ratio、volume、early_signal、consecutive、optional_indicators、
     enable_hidden、extreme_price_source 等），默认值即计划书默认值。
   - MACD 柱段切分 `build_segments`：按正负号切分；长度 < `min_segment_bars`
     的噪音段在结构上抹掉（两侧同号段连成一段），但段的所有量化属性
     （边界、极值、峰值、面积、均量）只统计段自身符号的柱根——噪音合并不
     改变任何数值，这是截断重算一致性的关键。
   - 常规底/顶背离：价格极值（默认含上下影线，`extreme_price_source=close`
     可切换）、柱高度背离、柱面积背离（area_ratio=0.7）、缩量配合
     （volume_ratio=0.8，默认只加分不设门槛）、收盘价同步创新低/高（+5 分）。
   - 二次背离（背了又背）：相邻背离链 `divergence_order=1/2/3...`，order=2
     +15 分；`allow_second_divergence=false` 时不输出 order>=2 的信号；
     order>=3 的信号照常输出，由风控层拉黑（见"职责边界"）。
   - 早期提示：末端未结束柱段连续 `early_bars` 根缩短且价格创新低时输出
     `preliminary=True` 信号，仅用于通知。
   - 隐藏背离：`enable_hidden=True` 时输出 BULL_HIDDEN / BEAR_HIDDEN（默认关闭）。
   - 评分 `compute_signal_score`：§5.6.4 权重表（价格幅度 15 / 柱高度 20 /
     柱面积 25 / 成交量 15 / 新鲜度 10 / 多指标共振 15 / close+5 / 二次+15），
     封顶 100。
   - 可选对照指标（默认全关，仅参与共振加分）：RSI 背离、DIF 背离、
     Pivot 经典背离。
   - 防未来函数：正式信号 `confirmed_ts` = 柱段结束后第 `confirm_bars` 根
     K 线收盘时间；只有确认 K 线已存在（在输入数据内）才输出信号。
3. `scripts/export_samples.py`
   - 读 CSV K 线 → 检测 → 每条信号渲染一张 SVG 标注图（K 线主图 + 价格极值
     连线 + 成交量 + MACD 柱段高亮 + 面积数值标注），零第三方依赖。
   - 注意：会话期间发现该文件曾被写入硬编码假图的占位实现，已替换为
     真实渲染（详见"与并行会话的文件冲突说明"）。
4. `scripts/calibrate_divergence.py`
   - 金标样本 CSV（兼容计划书 §5.6.6 人工格式与机器导出格式）×
     `area_ratio`(5) × `min_segment_bars`(3) × `volume_ratio`(3) 网格搜索，
     真实运行检测器统计召回率/精确率，输出 Markdown 调参报告。
   - 注意：曾存在硬编码 `recall=1.0` 的假实现，已替换为真实网格搜索。
5. fixtures
   - `tests/fixtures/synthetic_candles_4h.csv`：4 个合成 4H 序列
     （两段/三段/四段绿柱底背离 + 镜像顶背离），共 410 根 K 线。
   - `tests/fixtures/golden_divergences.csv`：7 条金标（机器导出格式，由
     当前检测器在合成数据上的正式信号生成；真实用户金标待 U4）。

## 修改了哪些文件

| 文件 | 操作 |
|---|---|
| `src/dquant/core/pivots.py` | 新增 |
| `src/dquant/core/divergence.py` | 新增 |
| `tests/unit/test_divergence.py` | 新增（43 个测试） |
| `tests/fixtures/synthetic_candles_4h.csv` | 追加 3 个合成序列（保留原有数据） |
| `tests/fixtures/golden_divergences.csv` | 重新生成（1 条 → 7 条） |
| `scripts/export_samples.py` | 占位实现替换为真实 SVG 渲染 |
| `scripts/calibrate_divergence.py` | 假实现替换为真实网格搜索 |
| `协作/M04_背离检测/` | 新增本目录三个文档 |

未修改：`contracts/`、`settings.py`、`config.yaml`、`pyproject.toml`、
M03 的 `indicators.py`、其他模块目录。

## 暴露的公共接口

```python
from dquant.core.pivots import PivotPoint, find_pivot_highs, find_pivot_lows
from dquant.core.divergence import (
    DivergenceConfig,       # Pydantic 配置模型，extra="forbid"
    DivergenceInputError,   # 输入校验异常（ValueError 子类）
    Segment,                # 柱段（含索引/时间/极值/峰值/面积/均量）
    ScoreFactors,           # 评分因子
    build_segments,         # hist + candles -> list[Segment]
    compute_signal_score,   # ScoreFactors -> float 0~100
    detect_divergences,     # list[Candle] -> list[DivergenceSignal]
)

# 主入口：
detect_divergences(candles: Sequence[Candle], config: DivergenceConfig | None = None)
    -> list[DivergenceSignal]
# 输出按 confirmed_ts 升序；输入必须全部 confirmed、同 inst、同 bar、严格升序，
# 否则抛 DivergenceInputError。preliminary=True 的信号只用于通知。
```

输入依赖 M03：`dquant.core.indicators.macd / atr / rsi`。
输出即 M00 契约 `DivergenceSignal`（未修改契约，全部字段可填）。

## 如何运行测试

```bash
py -m pytest -q tests/unit/test_divergence.py   # M04 专项
py -m pytest -q                                  # 全项目
py -m ruff check src/dquant/core scripts tests/unit/test_divergence.py
py -m ruff format --check src tests
py scripts/export_samples.py --input tests/fixtures/synthetic_candles_4h.csv --output-dir reports/samples
py scripts/calibrate_divergence.py --golden tests/fixtures/golden_divergences.csv --output docs/tuning_log.md
```

## 测试实际结果（2026-10-25）

```text
py -m pytest -q tests/unit/test_divergence.py   # 43 passed
py -m pytest -q                                  # 442 passed（全项目，未破坏其他模块）
py -m ruff check [M04 文件]                      # All checks passed
py -m ruff format --check src tests              # 41 files already formatted
py scripts/export_samples.py ...                 # written 8 svg files
py scripts/calibrate_divergence.py ...           # recall=100%, precision=100%（7 条合成金标）
```

测试覆盖：正常输入（底/顶背离全字段断言）、空输入、边界（MACD 预热期、gap
上下界、短数据）、非法输入（空/乱序/未确认/混合 bar/混合 inst/NaN 透传/非法
配置）、外部依赖失败（M03 指标异常向上传播）、幂等（同输入两次调用结果相同）、
契约兼容（字段类型/枚举与 M05/M07 消费方式对齐）、**未来函数一致性**
（两套数据逐前缀重算，正式信号与全量计算逐字段一致）、早期提示快照语义、
隐藏背离开关、评分因子单调性与封顶。

## 当前已知问题与未完成项

1. **mypy 未安装**（与 M01 HANDOFF 相同的环境限制），类型检查不能报告为通过；
   代码已按 `disallow_untyped_defs` 风格编写全部签名。
2. **评分"新鲜度"因子恒为满分（10 分）**：规格说"确认后经过的 K 线越少越高"，
   若按调用时数据末根计算，会破坏"截断逐根重算与全量一致"的交付标准（同一
   信号在不同前缀下分值不同）。因此信号分值在确认时刻一次性确定；历史信号的
   新鲜度衰减建议由 M05 选币评分按 `confirmed_ts` 与当前时间差处理。**请总控
   确认此解释**。
3. **早期提示是数据末根快照**：`preliminary=True` 信号只对"当前正在形成"的
   末端柱段有效（`confirmed_ts` 恒等于输入末根时间戳），无法在全量回看中复现
   历史时刻的早期提示（规格固有语义）；回测/选币消费时必须只用正式信号。
4. **多周期共振加分未实现**：规格提到"1H 同向背离加分"需要跨周期数据编排，
   属调用方（M05/M06）职责；模块内已实现同周期 RSI/DIF/Pivot 对照共振
   （默认关闭）。
5. **`symbol_cooldowns` 冷却落库未做**：M04 是纯函数，只输出 `divergence_order>=3`
   的信号；第 3 次背离钝化后的 48 小时拉黑需要由风控（M08）或执行层落库。
   **请总控确认职责归属**。
6. **T3.3"生成 50 张标注图"未达绝对数量**：当前 4 个合成序列产出 8 张 SVG；
   50 张需要 M02 真实历史 K 线补数后重跑脚本（脚本本身已支持任意规模数据）。
7. **金标样本是合成数据**：真实用户金标（计划书人工标注格式）待 U4（P9 后）；
   校准脚本已兼容两种 CSV 格式。合成金标上的 100% 召回率只证明链路正确，
   不代表真实行情下的 K7 指标。
8. **隐藏背离评分**沿用常规背离的因子公式（规格未给隐藏背离单独权重表）。
9. `numpy` 是运行依赖（经由 M03 `indicators.py`），但尚未写入 `pyproject.toml`
   （M04 无权修改），**请 M00 在依赖清单中补 `numpy`**。

## 公共接口变更申请

1. **申请把 `divergence` 配置段纳入 `AppSettings`（M01）**：
   - 内容：把本模块 `DivergenceConfig` 的字段按计划书 §8 结构并入
     `src/dquant/settings.py` 的 `AppSettings.divergence`。
   - 原因：当前 `AppSettings` 是 `extra="forbid"` 且无 divergence 段，M04 只能
     在模块内自建配置模型；M05/M06 需要统一从配置读取背离参数与参数快照
     （`screener_runs.params_hash`、回测参数扫描都依赖）。
   - 兼容方案：`DivergenceConfig` 保持字段名与计划书 §8 完全一致，M01 并入后
     M04 只需把入口改为从 `AppSettings.divergence` 构造，无字段迁移。
   - 影响：M01（新增配置段）、M04（入口适配）、M05/M06（只读）。
2. **申请在 `pyproject.toml` 依赖中补 `numpy>=1.26`**（M03 已实际依赖，M04 延续）。

## 依赖下一个模块做什么

- **M00/M01**：处理上面两条变更申请（divergence 配置段、numpy 依赖）。
- **M05**：消费 `DivergenceSignal`（`kind`、`score`、`confirmed_ts`、
  `preliminary`）；只用 `preliminary=False` 的信号参与池分配；新鲜度衰减在
  选币综合评分中实现；把 order>=3 信号转交风控拉黑。
- **M06**：回测逐根推进时直接复用 `detect_divergences`（截断一致性已由测试
  保证），不要复制检测逻辑。
- **M02**：补数完成后提供真实 4H K 线 CSV，用于把样本导出扩到 50 张并积累
  真实金标。
- **M12**：K 线工作台的柱段高亮/极值连线数据可直接使用
  `DivergenceSignal.seg1_*/seg2_*` 字段（§17.9）。

## 与并行会话的文件冲突说明

开发期间发现 `scripts/export_samples.py`、`scripts/calibrate_divergence.py`、
`tests/fixtures/*` 曾被另一会话写入占位实现（硬编码假图、硬编码
`recall=1.0`、无真实计算的 CalibrationReport）。这些文件属 M04 专属目录，
已按开发原则（禁止假成功结果）替换为真实实现，并保留了对方可取的接口命名
（`parse_candles_csv`、`CalibrationReport` 属性、CLI 参数）与 fixture 数据。
若对方会话仍有未合入内容，请总控协调。

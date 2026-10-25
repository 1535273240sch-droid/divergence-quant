"""M05 选币器纯函数核心。

把 M03 的趋势状态（RegimeResult）和 M04 的背离信号（DivergenceSignal）组合成
计划书 5.7 定义的 A～F 候选池，计算综合评分并按池排序截取 Top N。

本模块只做纯计算：不访问数据库、不访问网络、不修改全局状态。
输入输出全部使用 `dquant.contracts` 的公共模型。

综合评分 = 背离强度 × 0.6 + 趋势强度（|dist|、|slope| 归一化）× 0.3 + 流动性 × 0.1。
权重、归一化上限和 Top N 都由 ScreenerConfig 驱动；
在公共配置（AppSettings 的 screener 节）落地前，默认值取自计划书第 8 章的 screener 节。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dquant.contracts import (
    DivergenceKind,
    DivergenceSignal,
    RegimeResult,
    RegimeState,
    ScreenerCandidate,
)

POOL_LETTERS: frozenset[str] = frozenset("ABCDEF")

TRIGGER_SCHEDULED = "scheduled"
TRIGGER_MANUAL = "manual"
VALID_TRIGGERS: frozenset[str] = frozenset({TRIGGER_SCHEDULED, TRIGGER_MANUAL})

# 计划书 5.7 的池映射：底背离 = BULL_*，顶背离 = BEAR_*；隐藏背离与常规背离同侧同池。
_POOL_BY_STATE_AND_SIDE: dict[tuple[RegimeState, str], str] = {
    (RegimeState.UPTREND, "BULL"): "A",
    (RegimeState.DOWNTREND, "BEAR"): "B",
    (RegimeState.RANGE, "BULL"): "C",
    (RegimeState.RANGE, "BEAR"): "D",
    (RegimeState.UPTREND, "BEAR"): "E",
    (RegimeState.DOWNTREND, "BULL"): "E",
}


class ScreenerConfig(BaseModel):
    """选币器配置。字段与计划书第 8 章 screener 节保持一致，评分权重为 5.7 节的固定比例。"""

    model_config = ConfigDict(extra="forbid")

    pools_trade: list[str] = Field(default_factory=lambda: ["A", "B", "C", "D", "E"])
    pools_notify_only: list[str] = Field(default_factory=list)
    pool_size_multiplier: dict[str, float] = Field(
        default_factory=lambda: {"A": 1.0, "B": 1.0, "C": 0.5, "D": 0.5, "E": 0.3}
    )
    top_n_per_pool: int = Field(default=5, ge=1)
    weight_divergence: float = Field(default=0.6, ge=0, le=1)
    weight_trend: float = Field(default=0.3, ge=0, le=1)
    weight_liquidity: float = Field(default=0.1, ge=0, le=1)
    # dist = (close-ma)/ma，slope = MA200 的 k 根斜率；归一化上限可经回测校准。
    trend_dist_cap: float = Field(default=0.15, gt=0)
    trend_slope_cap: float = Field(default=0.05, gt=0)
    liquidity_quote_volume_cap: float = Field(default=100_000_000, gt=0)

    @field_validator("pools_trade", "pools_notify_only")
    @classmethod
    def validate_pool_letters(cls, value: list[str]) -> list[str]:
        """池编号只能是 A～F。"""

        for pool in value:
            if pool not in POOL_LETTERS:
                raise ValueError(f"unknown pool letter: {pool}")
        return value

    @field_validator("pool_size_multiplier")
    @classmethod
    def validate_pool_size_multiplier(cls, value: dict[str, float]) -> dict[str, float]:
        """仓位倍数必须显式落在 (0, 1] 区间。"""

        for pool, multiplier in value.items():
            if pool not in POOL_LETTERS:
                raise ValueError(f"unknown pool letter in pool_size_multiplier: {pool}")
            if not 0 < multiplier <= 1:
                raise ValueError(f"pool_size_multiplier[{pool}] must be within (0, 1]")
        return value

    @model_validator(mode="after")
    def validate_config(self) -> ScreenerConfig:
        """权重必须完整归一，且启用的池必须有仓位倍数。"""

        weight_sum = self.weight_divergence + self.weight_trend + self.weight_liquidity
        if abs(weight_sum - 1.0) > 1e-9:
            raise ValueError(f"screener weights must sum to 1, got {weight_sum:.6f}")
        for pool in [*self.pools_trade, *self.pools_notify_only]:
            if pool not in self.pool_size_multiplier:
                raise ValueError(f"pool_size_multiplier missing entry for pool {pool}")
        return self


@dataclass(frozen=True)
class ScreenerInputs:
    """一次扫描的全部输入。

    regimes: 每个交易对一条最新趋势快照（重复时取 ts_ms 最新的一条）。
    divergences: 当前有效的背离信号；preliminary=True 的早期提示会被忽略。
    liquidity: inst_id -> 24h 成交额（USDT），缺失视为流动性最低分。
    """

    regimes: Sequence[RegimeResult] = ()
    divergences: Sequence[DivergenceSignal] = ()
    liquidity: Mapping[str, Decimal] = field(default_factory=dict)


@dataclass(frozen=True)
class ScreenerRunRecord:
    """一次选币运行的完整结果。

    warnings 是运行期诊断信息（如背离没有对应趋势快照），不落库。
    """

    run_id: str
    trigger: str
    close_ts_ms: int | None
    started_ts_ms: int
    finished_ts_ms: int
    universe_size: int
    params_hash: str
    candidates: tuple[ScreenerCandidate, ...]
    warnings: tuple[str, ...] = ()


def assign_pool(state: RegimeState, kind: DivergenceKind | None) -> str:
    """按"趋势状态 × 背离类型"返回候选池编号；无背离进入 F 趋势清单。"""

    if kind is None:
        return "F"
    side = "BULL" if kind.value.startswith("BULL") else "BEAR"
    return _POOL_BY_STATE_AND_SIDE[(state, side)]


def trend_strength(regime: RegimeResult, config: ScreenerConfig) -> float:
    """把 |dist| 与 |slope| 各自按上限截断到 0~1 后取均值，放大到 0~100。"""

    dist_part = min(abs(regime.dist) / config.trend_dist_cap, 1.0)
    slope_part = min(abs(regime.slope) / config.trend_slope_cap, 1.0)
    return (dist_part + slope_part) / 2 * 100.0


def liquidity_strength(quote_volume_24h: Decimal, config: ScreenerConfig) -> float:
    """24h 成交额按上限归一到 0~100；负值属于上游数据错误，直接拒绝。"""

    if quote_volume_24h < 0:
        raise ValueError(f"liquidity must be non-negative, got {quote_volume_24h}")
    ratio = float(quote_volume_24h) / config.liquidity_quote_volume_cap
    return min(ratio, 1.0) * 100.0


def compute_score(
    divergence: DivergenceSignal | None,
    regime: RegimeResult,
    quote_volume_24h: Decimal | None,
    config: ScreenerConfig,
) -> float:
    """综合评分。无背离（F 池）时按趋势与流动性的权重占比重新归一到 0~100。"""

    trend = trend_strength(regime, config)
    liquidity = (
        liquidity_strength(quote_volume_24h, config) if quote_volume_24h is not None else 0.0
    )
    if divergence is None:
        denominator = config.weight_trend + config.weight_liquidity
        if denominator <= 0:
            raise ValueError("weights for trend and liquidity must not both be zero")
        return (trend * config.weight_trend + liquidity * config.weight_liquidity) / denominator
    return (
        divergence.score * config.weight_divergence
        + trend * config.weight_trend
        + liquidity * config.weight_liquidity
    )


def derive_divergence_id(signal: DivergenceSignal) -> str:
    """从信号自然键派生稳定的 divergence_id。

    契约中的 DivergenceSignal 没有 id 字段，而 screener_results 需要引用；
    这里用品种/周期/类型/柱段区间/序号/确认时间做 SHA-256，截断 32 位十六进制。
    评分不参与身份计算，参数校准后引用不会失效。
    """

    natural_key = "|".join(
        [
            signal.inst_id,
            signal.bar.value,
            signal.kind.value,
            str(signal.seg1_start_ts),
            str(signal.seg1_end_ts),
            str(signal.seg2_start_ts),
            str(signal.seg2_end_ts),
            str(signal.divergence_order),
            str(signal.confirmed_ts),
        ]
    )
    return hashlib.sha256(natural_key.encode("utf-8")).hexdigest()[:32]


def _params_hash(config: ScreenerConfig) -> str:
    """配置指纹：同一配置得到同一哈希，不同配置必然不同。"""

    return hashlib.sha256(config.model_dump_json().encode("utf-8")).hexdigest()


def _divergence_priority(signal: DivergenceSignal) -> tuple[float, int, str]:
    """多信号择优排序键：分高者优先，其次确认时间新者优先，最后按派生 id 保证确定性。"""

    return (-signal.score, -signal.confirmed_ts, derive_divergence_id(signal))


def run_screener(
    inputs: ScreenerInputs,
    config: ScreenerConfig,
    *,
    run_id: str,
    trigger: str,
    now_ts_ms: int,
    close_ts_ms: int | None = None,
) -> ScreenerRunRecord:
    """执行一次选币扫描。

    scheduled 触发必须携带 4H 收盘时间戳；manual 触发不携带。
    同样的输入、配置和时间戳必然得到相同结果，便于回放与排查。
    """

    if trigger not in VALID_TRIGGERS:
        raise ValueError(f"unknown trigger: {trigger}")
    if trigger == TRIGGER_SCHEDULED and close_ts_ms is None:
        raise ValueError("scheduled runs require close_ts_ms")

    for inst_id, quote_volume in inputs.liquidity.items():
        if quote_volume < 0:
            raise ValueError(f"liquidity must be non-negative for {inst_id}: {quote_volume}")

    warnings: list[str] = []
    regimes_by_inst: dict[str, RegimeResult] = {}
    for regime in inputs.regimes:
        current = regimes_by_inst.get(regime.inst_id)
        if current is None or regime.ts_ms > current.ts_ms:
            regimes_by_inst[regime.inst_id] = regime

    best_divergence: dict[str, DivergenceSignal] = {}
    for signal in inputs.divergences:
        # 早期提示只通知不下单，不进入候选池。
        if signal.preliminary:
            continue
        if signal.inst_id not in regimes_by_inst:
            warnings.append(f"divergence signal without regime snapshot: {signal.inst_id}")
            continue
        existing = best_divergence.get(signal.inst_id)
        if existing is None or _divergence_priority(signal) < _divergence_priority(existing):
            best_divergence[signal.inst_id] = signal

    scored: list[ScreenerCandidate] = []
    for inst_id, regime in regimes_by_inst.items():
        divergence = best_divergence.get(inst_id)
        pool = assign_pool(regime.state, divergence.kind if divergence else None)
        score = compute_score(divergence, regime, inputs.liquidity.get(inst_id), config)
        scored.append(
            ScreenerCandidate(
                inst_id=inst_id,
                pool=pool,
                regime=regime.state,
                divergence_id=derive_divergence_id(divergence) if divergence else None,
                divergence_kind=divergence.kind if divergence else None,
                score=score,
                rank=1,
                updated_ts_ms=now_ts_ms,
            )
        )

    ranked: list[ScreenerCandidate] = []
    for pool in sorted({candidate.pool for candidate in scored}):
        pool_candidates = sorted(
            (candidate for candidate in scored if candidate.pool == pool),
            key=lambda candidate: (-candidate.score, candidate.inst_id),
        )
        for rank, candidate in enumerate(pool_candidates[: config.top_n_per_pool], start=1):
            ranked.append(candidate.model_copy(update={"rank": rank}))

    return ScreenerRunRecord(
        run_id=run_id,
        trigger=trigger,
        close_ts_ms=close_ts_ms,
        started_ts_ms=now_ts_ms,
        finished_ts_ms=now_ts_ms,
        universe_size=len(regimes_by_inst),
        params_hash=_params_hash(config),
        candidates=tuple(ranked),
        warnings=tuple(sorted(warnings)),
    )

"""M05 选币器纯函数核心的单元测试。

覆盖：正常输入、空输入、边界输入、非法输入、配置校验、确定性与契约兼容性。
上游 M03/M04 尚未实现，本文件按公共契约直接构造 RegimeResult / DivergenceSignal fixture。
"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from dquant.contracts import (
    CandleBar,
    DivergenceKind,
    DivergenceSignal,
    RegimeResult,
    RegimeState,
    ScreenerCandidate,
)
from dquant.core.screener import (
    POOL_LETTERS,
    ScreenerConfig,
    ScreenerInputs,
    assign_pool,
    compute_score,
    derive_divergence_id,
    liquidity_strength,
    run_screener,
    trend_strength,
)

DAY_TS = 86_400_000
FOUR_HOUR_TS = 14_400_000
# 背离信号的段时间戳相对 confirmed_ts 向前推，必须保持非负，因此锚定一个固定基准。
_ANCHOR_TS = 100 * FOUR_HOUR_TS


def make_regime(
    inst_id: str = "BTC-USDT-SWAP",
    state: RegimeState = RegimeState.UPTREND,
    ts_ms: int = DAY_TS,
    dist: float = 0.05,
    slope: float = 0.02,
) -> RegimeResult:
    """构造一根日线趋势快照。"""

    return RegimeResult(
        inst_id=inst_id,
        bar=CandleBar.ONE_DAY,
        ts_ms=ts_ms,
        state=state,
        ma_type="SMA",
        dist=dist,
        slope=slope,
        above_ratio=0.9,
        cross_count=0,
        bars_in_state=10,
    )


def make_div(
    inst_id: str = "BTC-USDT-SWAP",
    kind: DivergenceKind = DivergenceKind.BULL_REGULAR,
    score: float = 80.0,
    confirmed_ts: int = FOUR_HOUR_TS * 2,
    preliminary: bool = False,
) -> DivergenceSignal:
    """构造一根 4H 背离信号；confirmed_ts 是相对锚点的偏移。"""

    confirm_ts = _ANCHOR_TS + confirmed_ts
    return DivergenceSignal(
        inst_id=inst_id,
        bar=CandleBar.FOUR_HOUR,
        kind=kind,
        seg1_start_ts=confirm_ts - FOUR_HOUR_TS * 6,
        seg1_end_ts=confirm_ts - FOUR_HOUR_TS * 3,
        seg1_extreme_ts=confirm_ts - FOUR_HOUR_TS * 5,
        seg1_extreme_price=Decimal("100"),
        seg1_peak=1.0,
        seg1_area=2.0,
        seg1_avg_vol=10.0,
        seg2_start_ts=confirm_ts - FOUR_HOUR_TS * 2,
        seg2_end_ts=confirm_ts - FOUR_HOUR_TS,
        seg2_extreme_ts=confirm_ts - FOUR_HOUR_TS,
        seg2_extreme_price=Decimal("90"),
        seg2_peak=0.5,
        seg2_area=1.0,
        seg2_avg_vol=8.0,
        height_div=True,
        area_div=True,
        volume_confirm=True,
        close_confirm=True,
        divergence_order=1,
        confirmed_ts=confirm_ts,
        score=score,
        preliminary=preliminary,
    )


# ---------------------------------------------------------------------------
# 池映射
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "kind", "expected"),
    [
        (RegimeState.UPTREND, DivergenceKind.BULL_REGULAR, "A"),
        (RegimeState.UPTREND, DivergenceKind.BULL_HIDDEN, "A"),
        (RegimeState.DOWNTREND, DivergenceKind.BEAR_REGULAR, "B"),
        (RegimeState.DOWNTREND, DivergenceKind.BEAR_HIDDEN, "B"),
        (RegimeState.RANGE, DivergenceKind.BULL_REGULAR, "C"),
        (RegimeState.RANGE, DivergenceKind.BULL_HIDDEN, "C"),
        (RegimeState.RANGE, DivergenceKind.BEAR_REGULAR, "D"),
        (RegimeState.RANGE, DivergenceKind.BEAR_HIDDEN, "D"),
        (RegimeState.UPTREND, DivergenceKind.BEAR_REGULAR, "E"),
        (RegimeState.DOWNTREND, DivergenceKind.BULL_REGULAR, "E"),
    ],
)
def test_assign_pool_covers_all_combinations(
    state: RegimeState, kind: DivergenceKind, expected: str
) -> None:
    assert assign_pool(state, kind) == expected


def test_assign_pool_without_divergence_is_f() -> None:
    assert assign_pool(RegimeState.RANGE, None) == "F"
    assert assign_pool(RegimeState.UPTREND, None) == "F"


def test_pool_letters_are_a_to_f() -> None:
    assert POOL_LETTERS == frozenset("ABCDEF")


# ---------------------------------------------------------------------------
# 评分
# ---------------------------------------------------------------------------


def test_trend_strength_scales_with_dist_and_slope() -> None:
    config = ScreenerConfig()
    regime = make_regime(dist=0.075, slope=0.05)

    # dist 0.075/0.15 = 0.5，slope 0.05/0.05 = 1.0，取均值 0.75。
    assert trend_strength(regime, config) == pytest.approx(75.0)


def test_trend_strength_ignores_direction() -> None:
    config = ScreenerConfig()
    long_regime = make_regime(dist=0.06, slope=0.03)
    short_regime = make_regime(dist=-0.06, slope=-0.03)

    assert trend_strength(long_regime, config) == pytest.approx(
        trend_strength(short_regime, config)
    )


def test_liquidity_strength_uses_quote_volume_cap() -> None:
    config = ScreenerConfig()
    assert liquidity_strength(Decimal("50000000"), config) == pytest.approx(50.0)
    assert liquidity_strength(Decimal("0"), config) == pytest.approx(0.0)


def test_compute_score_full_marks() -> None:
    config = ScreenerConfig()
    regime = make_regime(dist=0.2, slope=0.1)
    signal = make_div(score=100.0)

    score = compute_score(signal, regime, Decimal("100000000"), config)
    assert score == pytest.approx(100.0)


def test_compute_score_weighted_parts() -> None:
    config = ScreenerConfig()
    regime = make_regime(dist=0.05, slope=0.02)
    signal = make_div(score=80.0)

    # 背离 80×0.6=48；趋势 (0.5×(0.05/0.15)+0.5×(0.02/0.05))×100≈36.6667×0.3≈11.0；
    # 流动性 0.5×0.1×100=5。
    score = compute_score(signal, regime, Decimal("50000000"), config)
    assert score == pytest.approx(64.0)


def test_compute_score_caps_clamp_extreme_inputs() -> None:
    config = ScreenerConfig()
    regime = make_regime(dist=0.9, slope=0.5)
    signal = make_div(score=100.0)

    assert compute_score(signal, regime, Decimal("999999999"), config) == pytest.approx(100.0)


def test_compute_score_f_pool_renormalizes_without_divergence() -> None:
    config = ScreenerConfig()
    regime = make_regime(dist=0.2, slope=0.1)

    # 趋势满分 100×0.3=30，流动性 0.5×0.1×100=5，按 0.4 重新归一到 0~100。
    score = compute_score(None, regime, Decimal("50000000"), config)
    assert score == pytest.approx(87.5)


def test_compute_score_without_liquidity_is_not_error() -> None:
    config = ScreenerConfig()
    regime = make_regime(dist=0.05, slope=0.02)
    signal = make_div(score=80.0)

    score = compute_score(signal, regime, None, config)
    assert score == pytest.approx(59.0)


# ---------------------------------------------------------------------------
# 配置校验
# ---------------------------------------------------------------------------


def test_config_rejects_weights_not_summing_to_one() -> None:
    with pytest.raises(ValidationError, match="weights"):
        ScreenerConfig(weight_divergence=0.5, weight_trend=0.3, weight_liquidity=0.1)


def test_config_rejects_unknown_pool_letter() -> None:
    with pytest.raises(ValidationError, match="pool"):
        ScreenerConfig(pools_trade=["A", "G"])


def test_config_rejects_trade_pool_without_multiplier() -> None:
    with pytest.raises(ValidationError, match="pool_size_multiplier"):
        ScreenerConfig(pools_trade=["F"])


def test_config_rejects_multiplier_above_one() -> None:
    with pytest.raises(ValidationError, match="pool_size_multiplier"):
        ScreenerConfig(pool_size_multiplier={"A": 1.5, "B": 1.0, "C": 0.5, "D": 0.5, "E": 0.3})


def test_config_rejects_non_positive_top_n() -> None:
    with pytest.raises(ValidationError):
        ScreenerConfig(top_n_per_pool=0)


# ---------------------------------------------------------------------------
# 选币主流程
# ---------------------------------------------------------------------------


def test_run_screener_ranks_and_truncates_per_pool() -> None:
    config = ScreenerConfig(top_n_per_pool=2)
    regimes = [
        make_regime("AAA-USDT-SWAP", RegimeState.UPTREND, dist=0.06, slope=0.03),
        make_regime("BBB-USDT-SWAP", RegimeState.UPTREND, dist=0.05, slope=0.02),
        make_regime("CCC-USDT-SWAP", RegimeState.UPTREND, dist=0.04, slope=0.01),
    ]
    divergences = [
        make_div("AAA-USDT-SWAP", score=90.0),
        make_div("BBB-USDT-SWAP", score=80.0),
        make_div("CCC-USDT-SWAP", score=70.0),
    ]
    liquidity = {
        "AAA-USDT-SWAP": Decimal("30000000"),
        "BBB-USDT-SWAP": Decimal("20000000"),
        "CCC-USDT-SWAP": Decimal("10000000"),
    }
    record = run_screener(
        ScreenerInputs(regimes=regimes, divergences=divergences, liquidity=liquidity),
        config,
        run_id="run-1",
        trigger="manual",
        now_ts_ms=1_000,
    )

    pool_a = [c for c in record.candidates if c.pool == "A"]
    assert len(pool_a) == 2
    assert [c.inst_id for c in pool_a] == ["AAA-USDT-SWAP", "BBB-USDT-SWAP"]
    assert [c.rank for c in pool_a] == [1, 2]
    assert record.universe_size == 3
    assert record.warnings == ()


def test_run_screener_breaks_score_ties_by_inst_id() -> None:
    config = ScreenerConfig()
    regimes = [
        make_regime("ZZZ-USDT-SWAP", RegimeState.RANGE, dist=0.0, slope=0.0),
        make_regime("AAA-USDT-SWAP", RegimeState.RANGE, dist=0.0, slope=0.0),
    ]
    divergences = [
        make_div("ZZZ-USDT-SWAP", DivergenceKind.BEAR_REGULAR, score=50.0),
        make_div("AAA-USDT-SWAP", DivergenceKind.BEAR_REGULAR, score=50.0),
    ]
    record = run_screener(
        ScreenerInputs(regimes=regimes, divergences=divergences, liquidity={}),
        config,
        run_id="run-tie",
        trigger="manual",
        now_ts_ms=1_000,
    )

    pool_d = [c for c in record.candidates if c.pool == "D"]
    assert [c.inst_id for c in pool_d] == ["AAA-USDT-SWAP", "ZZZ-USDT-SWAP"]
    assert [c.rank for c in pool_d] == [1, 2]


def test_run_screener_puts_no_divergence_instruments_in_f_pool() -> None:
    config = ScreenerConfig()
    record = run_screener(
        ScreenerInputs(
            regimes=[make_regime("BTC-USDT-SWAP", RegimeState.UPTREND)],
            divergences=[],
            liquidity={"BTC-USDT-SWAP": Decimal("10000000")},
        ),
        config,
        run_id="run-f",
        trigger="manual",
        now_ts_ms=1_000,
    )

    assert len(record.candidates) == 1
    candidate = record.candidates[0]
    assert candidate.pool == "F"
    assert candidate.divergence_id is None
    assert candidate.divergence_kind is None
    assert candidate.regime == RegimeState.UPTREND


def test_run_screener_empty_universe_produces_valid_record() -> None:
    record = run_screener(
        ScreenerInputs(regimes=[], divergences=[], liquidity={}),
        ScreenerConfig(),
        run_id="run-empty",
        trigger="manual",
        now_ts_ms=1_000,
    )

    assert record.universe_size == 0
    assert record.candidates == ()
    assert record.warnings == ()


def test_run_screener_excludes_preliminary_signals() -> None:
    config = ScreenerConfig()
    record = run_screener(
        ScreenerInputs(
            regimes=[make_regime("BTC-USDT-SWAP", RegimeState.UPTREND)],
            divergences=[make_div("BTC-USDT-SWAP", score=99.0, preliminary=True)],
            liquidity={"BTC-USDT-SWAP": Decimal("10000000")},
        ),
        config,
        run_id="run-prelim",
        trigger="manual",
        now_ts_ms=1_000,
    )

    assert [c.pool for c in record.candidates] == ["F"]
    assert record.candidates[0].divergence_id is None


def test_run_screener_keeps_best_divergence_per_instrument() -> None:
    config = ScreenerConfig()
    better = make_div("BTC-USDT-SWAP", score=90.0, confirmed_ts=FOUR_HOUR_TS * 4)
    worse = make_div("BTC-USDT-SWAP", score=60.0, confirmed_ts=FOUR_HOUR_TS * 2)
    record = run_screener(
        ScreenerInputs(
            regimes=[make_regime("BTC-USDT-SWAP", RegimeState.UPTREND)],
            divergences=[worse, better],
            liquidity={},
        ),
        config,
        run_id="run-best",
        trigger="manual",
        now_ts_ms=1_000,
    )

    pool_a = [c for c in record.candidates if c.pool == "A"]
    assert len(pool_a) == 1
    assert pool_a[0].divergence_id == derive_divergence_id(better)


def test_run_screener_warns_on_divergence_without_regime() -> None:
    config = ScreenerConfig()
    record = run_screener(
        ScreenerInputs(
            regimes=[make_regime("BTC-USDT-SWAP", RegimeState.UPTREND)],
            divergences=[make_div("GHOST-USDT-SWAP", score=90.0)],
            liquidity={},
        ),
        config,
        run_id="run-orphan",
        trigger="manual",
        now_ts_ms=1_000,
    )

    assert record.universe_size == 1
    assert len(record.warnings) == 1
    assert "GHOST-USDT-SWAP" in record.warnings[0]
    assert all(c.inst_id == "BTC-USDT-SWAP" for c in record.candidates)


def test_run_screener_duplicate_regime_uses_latest_snapshot() -> None:
    config = ScreenerConfig()
    old = make_regime("BTC-USDT-SWAP", RegimeState.RANGE, ts_ms=DAY_TS, dist=0.5, slope=0.2)
    new = make_regime("BTC-USDT-SWAP", RegimeState.UPTREND, ts_ms=DAY_TS * 2)
    divergences = [make_div("BTC-USDT-SWAP", DivergenceKind.BEAR_REGULAR, score=50.0)]
    record = run_screener(
        ScreenerInputs(regimes=[old, new], divergences=divergences, liquidity={}),
        config,
        run_id="run-dup",
        trigger="manual",
        now_ts_ms=1_000,
    )

    # 旧快照若被采用会是 E 池（逆势），新快照 UPTREND + 顶背离也进 E 池，此处验证
    # 采用的是最新快照：dist=0.5/slope=0.2 会让旧快照的评分明显更高。
    candidate = next(c for c in record.candidates if c.pool == "E")
    best_possible = compute_score(divergences[0], new, None, config)
    assert candidate.score == pytest.approx(best_possible)


def test_run_screener_rejects_negative_liquidity() -> None:
    with pytest.raises(ValueError, match="liquidity"):
        run_screener(
            ScreenerInputs(
                regimes=[make_regime()],
                divergences=[],
                liquidity={"BTC-USDT-SWAP": Decimal("-1")},
            ),
            ScreenerConfig(),
            run_id="run-neg",
            trigger="manual",
            now_ts_ms=1_000,
        )


def test_run_screener_rejects_unknown_trigger() -> None:
    with pytest.raises(ValueError, match="trigger"):
        run_screener(
            ScreenerInputs(regimes=[], divergences=[], liquidity={}),
            ScreenerConfig(),
            run_id="run-bad",
            trigger="cron",
            now_ts_ms=1_000,
        )


def test_run_screener_scheduled_requires_close_ts() -> None:
    with pytest.raises(ValueError, match="close_ts_ms"):
        run_screener(
            ScreenerInputs(regimes=[], divergences=[], liquidity={}),
            ScreenerConfig(),
            run_id="sched-1",
            trigger="scheduled",
            now_ts_ms=1_000,
            close_ts_ms=None,
        )


def test_run_screener_is_deterministic_for_same_inputs() -> None:
    config = ScreenerConfig()
    inputs = ScreenerInputs(
        regimes=[make_regime("AAA-USDT-SWAP"), make_regime("BBB-USDT-SWAP", RegimeState.RANGE)],
        divergences=[
            make_div("AAA-USDT-SWAP", score=70.0),
            make_div("BBB-USDT-SWAP", DivergenceKind.BEAR_REGULAR, score=60.0),
        ],
        liquidity={"AAA-USDT-SWAP": Decimal("25000000")},
    )

    first = run_screener(inputs, config, run_id="run-d", trigger="manual", now_ts_ms=1_000)
    second = run_screener(inputs, config, run_id="run-d", trigger="manual", now_ts_ms=1_000)

    assert first == second


def test_run_screener_params_hash_tracks_config() -> None:
    inputs = ScreenerInputs(regimes=[make_regime()], divergences=[], liquidity={})
    strict = ScreenerConfig(top_n_per_pool=1)
    loose = ScreenerConfig(top_n_per_pool=5)

    strict_record = run_screener(inputs, strict, run_id="r1", trigger="manual", now_ts_ms=1)
    loose_record = run_screener(inputs, loose, run_id="r2", trigger="manual", now_ts_ms=1)

    assert strict_record.params_hash != loose_record.params_hash


def test_run_screener_candidates_match_contract() -> None:
    record = run_screener(
        ScreenerInputs(
            regimes=[make_regime("BTC-USDT-SWAP", RegimeState.UPTREND)],
            divergences=[make_div("BTC-USDT-SWAP", score=70.0)],
            liquidity={"BTC-USDT-SWAP": Decimal("25000000")},
        ),
        ScreenerConfig(),
        run_id="run-contract",
        trigger="scheduled",
        now_ts_ms=1_000,
        close_ts_ms=FOUR_HOUR_TS,
    )

    assert record.trigger == "scheduled"
    assert record.close_ts_ms == FOUR_HOUR_TS
    candidate = record.candidates[0]
    assert isinstance(candidate, ScreenerCandidate)
    assert set(candidate.model_dump()) == set(ScreenerCandidate.model_fields)
    assert candidate.updated_ts_ms == 1_000
    assert candidate.regime == RegimeState.UPTREND


def test_derive_divergence_id_is_stable_and_unique() -> None:
    base = make_div("BTC-USDT-SWAP", score=70.0)
    other_inst = make_div("ETH-USDT-SWAP", score=70.0)
    other_window = make_div("BTC-USDT-SWAP", score=70.0)
    other_window = other_window.model_copy(update={"seg2_end_ts": other_window.seg2_end_ts + 1})

    first = derive_divergence_id(base)
    # 身份由自然键（品种/周期/类型/柱段区间/序号/确认时间）决定，与评分无关：
    # 评分重算后引用不应失效。
    assert first == derive_divergence_id(make_div("BTC-USDT-SWAP", score=71.0))
    assert first != derive_divergence_id(other_inst)
    assert first != derive_divergence_id(other_window)
    assert len(first) == 32
    int(first, 16)  # 必须是十六进制字符串，能放进 String(64) 列。

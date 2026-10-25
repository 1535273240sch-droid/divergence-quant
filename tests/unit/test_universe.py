"""M03 币池初筛单元测试。

覆盖 USDT 计价、24h 成交额、上线历史、稳定币排除、黑白名单、
交易状态和 Top N 截取，以及所有非法输入的显式错误。
"""

from decimal import Decimal

import pytest

from dquant.contracts import Instrument, InstrumentState, Ticker
from dquant.core.universe import (
    UniverseConfig,
    UniverseReason,
    screen_universe,
)

MIN_VOLUME = Decimal("20000000")


def _instrument(
    inst_id: str,
    *,
    base: str | None = None,
    quote: str = "USDT",
    state: InstrumentState = InstrumentState.LIVE,
) -> Instrument:
    resolved_base = base if base is not None else inst_id.split("-")[0]
    return Instrument(
        inst_id=inst_id,
        base=resolved_base,
        quote=quote,
        ct_val=Decimal("0.01"),
        lot_sz=Decimal("1"),
        min_sz=Decimal("1"),
        tick_sz=Decimal("0.1"),
        state=state,
    )


def _ticker(inst_id: str, quote_volume: Decimal | int | str) -> Ticker:
    return Ticker(
        inst_id=inst_id,
        last=Decimal("100"),
        quote_volume_24h=Decimal(str(quote_volume)),
        ts_ms=1_700_000_000_000,
    )


# ---------------------------------------------------------------------------
# 正常筛选
# ---------------------------------------------------------------------------


def test_top_n_selection_and_ranking() -> None:
    instruments = [
        _instrument("AAA-USDT-SWAP"),
        _instrument("BBB-USDT-SWAP"),
        _instrument("CCC-USDT-SWAP"),
        _instrument("DDD-USDT-SWAP"),
    ]
    tickers = [
        _ticker("AAA-USDT-SWAP", 100_000_000),
        _ticker("BBB-USDT-SWAP", 50_000_000),
        _ticker("CCC-USDT-SWAP", 30_000_000),
        _ticker("DDD-USDT-SWAP", 25_000_000),
    ]
    history = {inst.inst_id: 400 for inst in instruments}
    config = UniverseConfig(top_n_by_volume=2)

    result = screen_universe(instruments, tickers, history, config=config)

    assert result.selected_inst_ids == ["AAA-USDT-SWAP", "BBB-USDT-SWAP"]
    assert [item.rank for item in result.selected] == [1, 2]
    assert all(not item.whitelisted for item in result.selected)
    assert result.reason_for("CCC-USDT-SWAP") is UniverseReason.BELOW_TOP_N
    assert result.reason_for("DDD-USDT-SWAP") is UniverseReason.BELOW_TOP_N


def test_top_n_none_keeps_all_eligible() -> None:
    instruments = [_instrument(f"C{index}-USDT-SWAP") for index in range(3)]
    tickers = [_ticker(inst.inst_id, 30_000_000) for inst in instruments]
    history = {inst.inst_id: 300 for inst in instruments}
    config = UniverseConfig(top_n_by_volume=None)

    result = screen_universe(instruments, tickers, history, config=config)

    assert len(result.selected_inst_ids) == 3


def test_rank_tie_breaks_by_inst_id() -> None:
    instruments = [_instrument("BTC-USDT-SWAP"), _instrument("AAA-USDT-SWAP")]
    tickers = [
        _ticker("BTC-USDT-SWAP", 50_000_000),
        _ticker("AAA-USDT-SWAP", 50_000_000),
    ]
    history = {"BTC-USDT-SWAP": 400, "AAA-USDT-SWAP": 400}

    result = screen_universe(instruments, tickers, history)

    assert result.selected_inst_ids == ["AAA-USDT-SWAP", "BTC-USDT-SWAP"]
    assert [item.rank for item in result.selected] == [1, 2]


def test_ticker_mapping_is_accepted() -> None:
    instruments = [_instrument("AAA-USDT-SWAP")]
    tickers = {"AAA-USDT-SWAP": _ticker("AAA-USDT-SWAP", 50_000_000)}
    result = screen_universe(instruments, tickers, {"AAA-USDT-SWAP": 300})
    assert result.selected_inst_ids == ["AAA-USDT-SWAP"]


# ---------------------------------------------------------------------------
# 单项过滤规则
# ---------------------------------------------------------------------------


def test_quote_must_match() -> None:
    instruments = [_instrument("AAA-USDC-SWAP", quote="USDC")]
    result = screen_universe(
        instruments,
        [_ticker("AAA-USDC-SWAP", 50_000_000)],
        {"AAA-USDC-SWAP": 400},
    )
    assert result.reason_for("AAA-USDC-SWAP") is UniverseReason.QUOTE_NOT_SUPPORTED


def test_stablecoin_base_is_excluded() -> None:
    instruments = [_instrument("USDC-USDT-SWAP", base="USDC")]
    tickers = [_ticker("USDC-USDT-SWAP", 500_000_000)]
    history = {"USDC-USDT-SWAP": 500}

    result = screen_universe(instruments, tickers, history)
    assert result.reason_for("USDC-USDT-SWAP") is UniverseReason.STABLECOIN_BASE

    result = screen_universe(
        instruments,
        tickers,
        history,
        config=UniverseConfig(exclude_stablecoins=False),
    )
    assert "USDC-USDT-SWAP" in result.selected_inst_ids


def test_low_quote_volume_is_excluded() -> None:
    instruments = [_instrument("AAA-USDT-SWAP")]
    result = screen_universe(
        instruments,
        [_ticker("AAA-USDT-SWAP", MIN_VOLUME - 1)],
        {"AAA-USDT-SWAP": 400},
    )
    assert result.reason_for("AAA-USDT-SWAP") is UniverseReason.LOW_QUOTE_VOLUME


def test_quote_volume_equal_to_threshold_is_allowed() -> None:
    instruments = [_instrument("AAA-USDT-SWAP")]
    result = screen_universe(
        instruments,
        [_ticker("AAA-USDT-SWAP", MIN_VOLUME)],
        {"AAA-USDT-SWAP": 400},
    )
    assert result.selected_inst_ids == ["AAA-USDT-SWAP"]


def test_insufficient_history_is_excluded() -> None:
    instruments = [_instrument("AAA-USDT-SWAP")]
    result = screen_universe(
        instruments,
        [_ticker("AAA-USDT-SWAP", 50_000_000)],
        {"AAA-USDT-SWAP": 199},
    )
    assert result.reason_for("AAA-USDT-SWAP") is UniverseReason.INSUFFICIENT_HISTORY


def test_history_equal_to_threshold_is_allowed() -> None:
    instruments = [_instrument("AAA-USDT-SWAP")]
    result = screen_universe(
        instruments,
        [_ticker("AAA-USDT-SWAP", 50_000_000)],
        {"AAA-USDT-SWAP": 200},
    )
    assert result.selected_inst_ids == ["AAA-USDT-SWAP"]


def test_missing_ticker_is_excluded() -> None:
    instruments = [_instrument("AAA-USDT-SWAP")]
    result = screen_universe(instruments, [], {"AAA-USDT-SWAP": 400})
    assert result.reason_for("AAA-USDT-SWAP") is UniverseReason.MISSING_TICKER


def test_missing_history_is_excluded() -> None:
    instruments = [_instrument("AAA-USDT-SWAP")]
    result = screen_universe(instruments, [_ticker("AAA-USDT-SWAP", 50_000_000)], {})
    assert result.reason_for("AAA-USDT-SWAP") is UniverseReason.MISSING_HISTORY


def test_non_live_instrument_is_excluded() -> None:
    instruments = [
        _instrument("AAA-USDT-SWAP", state=InstrumentState.SUSPENDED),
        _instrument("BBB-USDT-SWAP", state=InstrumentState.EXPIRED),
    ]
    tickers = [
        _ticker("AAA-USDT-SWAP", 50_000_000),
        _ticker("BBB-USDT-SWAP", 50_000_000),
    ]
    history = {"AAA-USDT-SWAP": 400, "BBB-USDT-SWAP": 400}
    result = screen_universe(instruments, tickers, history)
    assert result.reason_for("AAA-USDT-SWAP") is UniverseReason.NOT_LIVE
    assert result.reason_for("BBB-USDT-SWAP") is UniverseReason.NOT_LIVE


# ---------------------------------------------------------------------------
# 黑白名单
# ---------------------------------------------------------------------------


def test_blacklist_is_excluded() -> None:
    instruments = [_instrument("AAA-USDT-SWAP")]
    result = screen_universe(
        instruments,
        [_ticker("AAA-USDT-SWAP", 50_000_000)],
        {"AAA-USDT-SWAP": 400},
        config=UniverseConfig(blacklist=("AAA-USDT-SWAP",)),
    )
    assert result.reason_for("AAA-USDT-SWAP") is UniverseReason.BLACKLISTED


def test_whitelist_forces_inclusion_and_bypasses_filters() -> None:
    instruments = [
        _instrument("AAA-USDT-SWAP"),
        _instrument("BBB-USDT-SWAP"),
    ]
    tickers = [
        _ticker("AAA-USDT-SWAP", 50_000_000),
        _ticker("BBB-USDT-SWAP", 1_000_000),
    ]
    history = {"AAA-USDT-SWAP": 400, "BBB-USDT-SWAP": 10}
    config = UniverseConfig(whitelist=("BBB-USDT-SWAP",))

    result = screen_universe(instruments, tickers, history, config=config)

    assert result.selected_inst_ids == ["AAA-USDT-SWAP", "BBB-USDT-SWAP"]
    whitelisted = [item for item in result.selected if item.whitelisted]
    assert [item.inst_id for item in whitelisted] == ["BBB-USDT-SWAP"]
    assert result.reason_for("BBB-USDT-SWAP") is UniverseReason.WHITELISTED


def test_whitelist_tolerates_missing_ticker_and_history() -> None:
    instruments = [_instrument("BBB-USDT-SWAP")]
    config = UniverseConfig(whitelist=("BBB-USDT-SWAP",))
    result = screen_universe(instruments, [], {}, config=config)
    assert result.selected_inst_ids == ["BBB-USDT-SWAP"]
    assert result.selected[0].quote_volume_24h is None


def test_whitelist_cannot_bypass_non_live_state() -> None:
    instruments = [
        _instrument("AAA-USDT-SWAP", state=InstrumentState.SUSPENDED),
    ]
    config = UniverseConfig(whitelist=("AAA-USDT-SWAP",))
    result = screen_universe(
        instruments,
        [_ticker("AAA-USDT-SWAP", 50_000_000)],
        {"AAA-USDT-SWAP": 400},
        config=config,
    )
    assert result.reason_for("AAA-USDT-SWAP") is UniverseReason.NOT_LIVE


def test_blacklist_wins_over_whitelist() -> None:
    instruments = [_instrument("AAA-USDT-SWAP")]
    config = UniverseConfig(
        whitelist=("AAA-USDT-SWAP",),
        blacklist=("AAA-USDT-SWAP",),
    )
    result = screen_universe(
        instruments,
        [_ticker("AAA-USDT-SWAP", 50_000_000)],
        {"AAA-USDT-SWAP": 400},
        config=config,
    )
    assert result.reason_for("AAA-USDT-SWAP") is UniverseReason.BLACKLISTED
    assert result.selected_inst_ids == []


def test_whitelist_does_not_consume_top_n_slots() -> None:
    instruments = [
        _instrument("AAA-USDT-SWAP"),
        _instrument("BBB-USDT-SWAP"),
        _instrument("CCC-USDT-SWAP"),
    ]
    tickers = [
        _ticker("AAA-USDT-SWAP", 100_000_000),
        _ticker("BBB-USDT-SWAP", 90_000_000),
        _ticker("CCC-USDT-SWAP", 80_000_000),
    ]
    history = {inst.inst_id: 400 for inst in instruments}
    config = UniverseConfig(top_n_by_volume=1, whitelist=("BBB-USDT-SWAP",))

    result = screen_universe(instruments, tickers, history, config=config)

    assert set(result.selected_inst_ids) == {"AAA-USDT-SWAP", "BBB-USDT-SWAP"}
    assert result.reason_for("CCC-USDT-SWAP") is UniverseReason.BELOW_TOP_N


def test_whitelist_not_listed_is_reported() -> None:
    instruments = [_instrument("AAA-USDT-SWAP")]
    config = UniverseConfig(whitelist=("XYZ-USDT-SWAP",))
    result = screen_universe(
        instruments,
        [_ticker("AAA-USDT-SWAP", 50_000_000)],
        {"AAA-USDT-SWAP": 400},
        config=config,
    )
    assert result.reason_for("XYZ-USDT-SWAP") is UniverseReason.WHITELIST_NOT_LISTED
    assert result.reason_for("XYZ-USDT-SWAP") is not None


# ---------------------------------------------------------------------------
# 完整性与纯函数
# ---------------------------------------------------------------------------


def test_empty_inputs_return_empty_result() -> None:
    result = screen_universe([], [], {})
    assert result.selected == ()
    assert result.excluded == ()


def test_every_instrument_has_exactly_one_decision() -> None:
    instruments = [
        _instrument("AAA-USDT-SWAP"),
        _instrument("BBB-USDC-SWAP", quote="USDC"),
        _instrument("CCC-USDT-SWAP", state=InstrumentState.SUSPENDED),
        _instrument("USDC-USDT-SWAP", base="USDC"),
        _instrument("DDD-USDT-SWAP"),
        _instrument("EEE-USDT-SWAP"),
    ]
    tickers = [
        _ticker("AAA-USDT-SWAP", 100_000_000),
        _ticker("BBB-USDC-SWAP", 100_000_000),
        _ticker("CCC-USDT-SWAP", 100_000_000),
        _ticker("USDC-USDT-SWAP", 100_000_000),
        _ticker("DDD-USDT-SWAP", 1_000),
        _ticker("EEE-USDT-SWAP", 90_000_000),
    ]
    history = {inst.inst_id: 400 for inst in instruments}
    history["EEE-USDT-SWAP"] = 5
    config = UniverseConfig(
        top_n_by_volume=10,
        whitelist=("EEE-USDT-SWAP",),
        blacklist=("DDD-USDT-SWAP",),
    )

    result = screen_universe(instruments, tickers, history, config=config)

    decided = list(result.selected_inst_ids) + list(result.excluded_inst_ids)
    assert sorted(decided) == sorted(inst.inst_id for inst in instruments)
    assert len(decided) == len(set(decided))


def test_inputs_are_not_mutated() -> None:
    instruments = [_instrument("AAA-USDT-SWAP"), _instrument("BBB-USDT-SWAP")]
    tickers = [
        _ticker("AAA-USDT-SWAP", 50_000_000),
        _ticker("BBB-USDT-SWAP", 60_000_000),
    ]
    history = {"AAA-USDT-SWAP": 400, "BBB-USDT-SWAP": 400}
    config = UniverseConfig(whitelist=("AAA-USDT-SWAP",))

    snapshot_inst = list(instruments)
    snapshot_tickers = list(tickers)
    snapshot_history = dict(history)

    screen_universe(instruments, tickers, history, config=config)

    assert instruments == snapshot_inst
    assert tickers == snapshot_tickers
    assert history == snapshot_history


def test_repeated_calls_are_deterministic() -> None:
    instruments = [_instrument("AAA-USDT-SWAP"), _instrument("BBB-USDT-SWAP")]
    tickers = [
        _ticker("AAA-USDT-SWAP", 50_000_000),
        _ticker("BBB-USDT-SWAP", 50_000_000),
    ]
    history = {"AAA-USDT-SWAP": 400, "BBB-USDT-SWAP": 400}

    first = screen_universe(instruments, tickers, history)
    second = screen_universe(instruments, tickers, history)

    assert [(item.inst_id, item.rank) for item in first.selected] == [
        (item.inst_id, item.rank) for item in second.selected
    ]
    assert [(item.inst_id, item.reason) for item in first.excluded] == [
        (item.inst_id, item.reason) for item in second.excluded
    ]


# ---------------------------------------------------------------------------
# 非法输入
# ---------------------------------------------------------------------------


def test_duplicate_instrument_is_rejected() -> None:
    instruments = [_instrument("AAA-USDT-SWAP"), _instrument("AAA-USDT-SWAP")]
    with pytest.raises(ValueError, match="duplicate"):
        screen_universe(instruments, [], {})


def test_duplicate_ticker_is_rejected() -> None:
    instruments = [_instrument("AAA-USDT-SWAP")]
    tickers = [
        _ticker("AAA-USDT-SWAP", 50_000_000),
        _ticker("AAA-USDT-SWAP", 60_000_000),
    ]
    with pytest.raises(ValueError, match="duplicate"):
        screen_universe(instruments, tickers, {"AAA-USDT-SWAP": 400})


def test_negative_history_is_rejected() -> None:
    instruments = [_instrument("AAA-USDT-SWAP")]
    with pytest.raises(ValueError, match="history"):
        screen_universe(
            instruments,
            [_ticker("AAA-USDT-SWAP", 50_000_000)],
            {"AAA-USDT-SWAP": -1},
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"min_quote_volume_24h": Decimal("-1")},
        {"min_history_bars": 0},
        {"top_n_by_volume": 0},
        {"top_n_by_volume": -5},
        {"quote": ""},
    ],
)
def test_config_validation(overrides: dict) -> None:
    with pytest.raises(ValueError):
        UniverseConfig(**overrides)

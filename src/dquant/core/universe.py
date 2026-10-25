"""币池初筛：把交易对元数据、24h 行情和历史 K 线数量转换为可扫描币池。

本模块是纯函数实现：不访问数据库、不访问网络、不修改全局状态。
所有未入选的交易对都会给出明确原因，不允许静默丢弃。

默认规则对应计划书 §5.4：

- 只保留 ``quote``（默认 USDT）计价、状态为 ``live`` 的交易对；
- 排除稳定币基础币、黑名单和成交额不足的币种；
- 历史 K 线不足 ``min_history_bars``（默认 200）的币种无法计算 MA200；
- 按 24h 成交额取前 ``top_n_by_volume``（默认 150）名；
- 白名单强制纳入，可绕过成交额、历史长度和 Top N 限制；
- 黑名单优先级高于白名单。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from dquant.contracts import Instrument, InstrumentState, Ticker

DEFAULT_STABLECOIN_BASES = frozenset(
    {
        "BUSD",
        "DAI",
        "EURC",
        "EURS",
        "EURT",
        "FDUSD",
        "FRAX",
        "GUSD",
        "LUSD",
        "PYUSD",
        "TUSD",
        "USDC",
        "USDD",
        "USDE",
        "USDS",
        "USDT",
    }
)


class UniverseReason(StrEnum):
    """交易对入选或排除的原因。"""

    SELECTED = "selected"
    WHITELISTED = "whitelisted"
    QUOTE_NOT_SUPPORTED = "quote_not_supported"
    STABLECOIN_BASE = "stablecoin_base"
    BLACKLISTED = "blacklisted"
    NOT_LIVE = "not_live"
    MISSING_TICKER = "missing_ticker"
    LOW_QUOTE_VOLUME = "low_quote_volume"
    MISSING_HISTORY = "missing_history"
    INSUFFICIENT_HISTORY = "insufficient_history"
    BELOW_TOP_N = "below_top_n"
    WHITELIST_NOT_LISTED = "whitelist_not_listed"


def _default_min_volume() -> Decimal:
    return Decimal("20000000")


@dataclass(frozen=True)
class UniverseConfig:
    """币池初筛参数，默认值与计划书 §5.4 和 §8 一致。"""

    quote: str = "USDT"
    min_quote_volume_24h: Decimal = field(default_factory=_default_min_volume)
    min_history_bars: int = 200
    top_n_by_volume: int | None = 150
    whitelist: tuple[str, ...] = ()
    blacklist: tuple[str, ...] = ()
    exclude_stablecoins: bool = True
    stablecoin_bases: frozenset[str] = DEFAULT_STABLECOIN_BASES

    def __post_init__(self) -> None:
        if not isinstance(self.quote, str) or not self.quote.strip():
            raise ValueError("quote must be a non-empty string")
        object.__setattr__(self, "quote", self.quote.strip().upper())

        volume = self.min_quote_volume_24h
        if not isinstance(volume, Decimal):
            try:
                volume = Decimal(str(volume))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError("min_quote_volume_24h must be a decimal number") from exc
            object.__setattr__(self, "min_quote_volume_24h", volume)
        if volume < 0:
            raise ValueError("min_quote_volume_24h must not be negative")

        if (
            isinstance(self.min_history_bars, bool)
            or not isinstance(self.min_history_bars, int)
            or self.min_history_bars <= 0
        ):
            raise ValueError("min_history_bars must be a positive integer")

        if self.top_n_by_volume is not None:
            if (
                isinstance(self.top_n_by_volume, bool)
                or not isinstance(self.top_n_by_volume, int)
                or self.top_n_by_volume <= 0
            ):
                raise ValueError("top_n_by_volume must be a positive integer or None")

        object.__setattr__(self, "whitelist", _normalize_inst_ids(self.whitelist, "whitelist"))
        object.__setattr__(self, "blacklist", _normalize_inst_ids(self.blacklist, "blacklist"))
        object.__setattr__(
            self,
            "stablecoin_bases",
            frozenset(str(base).strip().upper() for base in self.stablecoin_bases),
        )


def _normalize_inst_ids(values: Sequence[str], name: str) -> tuple[str, ...]:
    """校验并规范化交易对列表，去重后保持确定性顺序。"""

    if isinstance(values, (str, bytes)):
        raise ValueError(f"{name} must be a sequence of inst_id strings")
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must contain non-empty inst_id strings")
        inst_id = value.strip()
        if inst_id not in seen:
            normalized.append(inst_id)
            seen.add(inst_id)
    return tuple(normalized)


@dataclass(frozen=True)
class UniverseSelection:
    """入选交易对及其排名。"""

    inst_id: str
    rank: int
    quote_volume_24h: Decimal | None
    whitelisted: bool


@dataclass(frozen=True)
class UniverseExclusion:
    """未入选交易对及原因。"""

    inst_id: str
    reason: UniverseReason
    detail: str


@dataclass(frozen=True)
class UniverseResult:
    """币池初筛结果，入选和排除交易对都有完整记录。"""

    selected: tuple[UniverseSelection, ...]
    excluded: tuple[UniverseExclusion, ...]

    @property
    def selected_inst_ids(self) -> list[str]:
        return [item.inst_id for item in self.selected]

    @property
    def excluded_inst_ids(self) -> list[str]:
        return [item.inst_id for item in self.excluded]

    def reason_for(self, inst_id: str) -> UniverseReason | None:
        """查询指定交易对的入选或排除原因，未出现时返回 ``None``。"""

        for item in self.selected:
            if item.inst_id == inst_id:
                return UniverseReason.WHITELISTED if item.whitelisted else UniverseReason.SELECTED
        for item in self.excluded:
            if item.inst_id == inst_id:
                return item.reason
        return None


def _normalize_tickers(
    tickers: Sequence[Ticker] | Mapping[str, Ticker],
) -> dict[str, Ticker]:
    """把行情快照统一为 ``inst_id -> Ticker``，并拒绝重复数据。"""

    if isinstance(tickers, Mapping):
        result: dict[str, Ticker] = {}
        for key, ticker in tickers.items():
            if not isinstance(ticker, Ticker):
                raise TypeError("tickers must contain Ticker objects")
            if ticker.inst_id != key:
                raise ValueError(f"ticker key mismatch: {key} != {ticker.inst_id}")
            result[key] = ticker
        return result

    if isinstance(tickers, (str, bytes)) or not isinstance(tickers, Sequence):
        raise TypeError("tickers must be a sequence or mapping of Ticker objects")

    sequence_result: dict[str, Ticker] = {}
    for ticker in tickers:
        if not isinstance(ticker, Ticker):
            raise TypeError("tickers must contain Ticker objects")
        if ticker.inst_id in sequence_result:
            raise ValueError(f"duplicate ticker for {ticker.inst_id}")
        sequence_result[ticker.inst_id] = ticker
    return sequence_result


def _validate_history_bars(history_bars: Mapping[str, int]) -> None:
    """历史 K 线数量必须是非负整数。"""

    if not isinstance(history_bars, Mapping):
        raise TypeError("history_bars must be a mapping of inst_id to bar count")
    for inst_id, count in history_bars.items():
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(f"history_bars must be non-negative integers: {inst_id}")


def _validate_instruments(instruments: Sequence[Instrument]) -> None:
    """校验交易对列表，重复 inst_id 视为数据错误。"""

    if isinstance(instruments, (str, bytes)) or not isinstance(instruments, Sequence):
        raise TypeError("instruments must be a sequence of Instrument objects")
    seen: set[str] = set()
    for instrument in instruments:
        if not isinstance(instrument, Instrument):
            raise TypeError("instruments must contain Instrument objects")
        if instrument.inst_id in seen:
            raise ValueError(f"duplicate instrument: {instrument.inst_id}")
        seen.add(instrument.inst_id)


def _sort_key(entry: tuple[Instrument, Ticker | None]) -> tuple[bool, Decimal, str]:
    """按成交额降序、inst_id 升序排序；缺失成交额排在最后。"""

    instrument, ticker = entry
    volume = ticker.quote_volume_24h if ticker is not None else None
    return (volume is None, -(volume if volume is not None else Decimal(0)), instrument.inst_id)


def screen_universe(
    instruments: Sequence[Instrument],
    tickers: Sequence[Ticker] | Mapping[str, Ticker],
    history_bars: Mapping[str, int],
    *,
    config: UniverseConfig | None = None,
) -> UniverseResult:
    """执行币池初筛，返回入选排名和全部排除原因。"""

    resolved = config or UniverseConfig()
    _validate_instruments(instruments)
    _validate_history_bars(history_bars)
    ticker_map = _normalize_tickers(tickers)
    whitelist = set(resolved.whitelist)
    blacklist = set(resolved.blacklist)

    exclusions: list[UniverseExclusion] = []
    candidates: list[tuple[Instrument, Ticker]] = []
    whitelisted_entries: list[tuple[Instrument, Ticker | None]] = []

    for instrument in instruments:
        inst_id = instrument.inst_id
        ticker = ticker_map.get(inst_id)

        if inst_id in blacklist:
            exclusions.append(
                UniverseExclusion(inst_id, UniverseReason.BLACKLISTED, "inst_id is blacklisted")
            )
            continue
        if instrument.state is not InstrumentState.LIVE:
            exclusions.append(
                UniverseExclusion(
                    inst_id,
                    UniverseReason.NOT_LIVE,
                    f"instrument state is {instrument.state.value}",
                )
            )
            continue
        if inst_id in whitelist:
            whitelisted_entries.append((instrument, ticker))
            continue

        if instrument.quote.strip().upper() != resolved.quote:
            exclusions.append(
                UniverseExclusion(
                    inst_id,
                    UniverseReason.QUOTE_NOT_SUPPORTED,
                    f"quote={instrument.quote} != {resolved.quote}",
                )
            )
            continue
        if resolved.exclude_stablecoins and instrument.base.strip().upper() in (
            resolved.stablecoin_bases
        ):
            exclusions.append(
                UniverseExclusion(
                    inst_id,
                    UniverseReason.STABLECOIN_BASE,
                    f"base={instrument.base} is a stablecoin",
                )
            )
            continue
        if ticker is None:
            exclusions.append(
                UniverseExclusion(inst_id, UniverseReason.MISSING_TICKER, "no 24h ticker")
            )
            continue
        if ticker.quote_volume_24h < resolved.min_quote_volume_24h:
            exclusions.append(
                UniverseExclusion(
                    inst_id,
                    UniverseReason.LOW_QUOTE_VOLUME,
                    f"quote_volume_24h={ticker.quote_volume_24h} < {resolved.min_quote_volume_24h}",
                )
            )
            continue
        bars = history_bars.get(inst_id)
        if bars is None:
            exclusions.append(
                UniverseExclusion(
                    inst_id, UniverseReason.MISSING_HISTORY, "no confirmed history bar count"
                )
            )
            continue
        if bars < resolved.min_history_bars:
            exclusions.append(
                UniverseExclusion(
                    inst_id,
                    UniverseReason.INSUFFICIENT_HISTORY,
                    f"history_bars={bars} < {resolved.min_history_bars}",
                )
            )
            continue
        candidates.append((instrument, ticker))

    candidates.sort(key=_sort_key)
    if resolved.top_n_by_volume is None:
        ranked_candidates = candidates
    else:
        ranked_candidates = candidates[: resolved.top_n_by_volume]
        for instrument, _ in candidates[resolved.top_n_by_volume :]:
            exclusions.append(
                UniverseExclusion(
                    instrument.inst_id,
                    UniverseReason.BELOW_TOP_N,
                    f"ranked below top {resolved.top_n_by_volume}",
                )
            )

    included = sorted(whitelisted_entries + ranked_candidates, key=_sort_key)
    selected = tuple(
        UniverseSelection(
            inst_id=instrument.inst_id,
            rank=position,
            quote_volume_24h=ticker.quote_volume_24h if ticker is not None else None,
            whitelisted=instrument.inst_id in whitelist,
        )
        for position, (instrument, ticker) in enumerate(included, start=1)
    )

    listed = {instrument.inst_id for instrument in instruments}
    for inst_id in sorted(whitelist - listed):
        exclusions.append(
            UniverseExclusion(
                inst_id,
                UniverseReason.WHITELIST_NOT_LISTED,
                "whitelist entry not found in instruments",
            )
        )

    exclusions.sort(key=lambda item: item.inst_id)
    return UniverseResult(selected=selected, excluded=tuple(exclusions))


__all__ = [
    "DEFAULT_STABLECOIN_BASES",
    "UniverseConfig",
    "UniverseExclusion",
    "UniverseReason",
    "UniverseResult",
    "UniverseSelection",
    "screen_universe",
]

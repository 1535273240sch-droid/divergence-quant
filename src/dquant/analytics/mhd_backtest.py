"""MHD 多周期历史绩效回测。

使用与实盘 MHD 完全相同的 ``detect_mhd`` 信号、ATR 初始止损和 ATR 移动止损，
对 5m/15m/1H/4H/1D 逐周期做可重复的历史模拟，并把结果写入 ``backtest_runs`` 表，
供 API 和前端展示“哪个周期收益最高”。
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from dquant.contracts import CandleBar
from dquant.core.mhd import MHDConfig, detect_mhd
from dquant.db.models import BacktestRunModel, Base
from dquant.strategies.atr_stop import initial_stop, trailing_stop

DEFAULT_TIMEFRAMES = ("5m", "15m", "1H", "4H", "1D")
DEFAULT_MAX_BARS = 5000
DEFAULT_ATR_MULT = Decimal("2.5")


@dataclass(frozen=True)
class MHDTrade:
    inst_id: str
    side: str
    entry_ts_ms: int
    exit_ts_ms: int
    entry_price: float
    exit_price: float
    return_pct: float
    bars_held: int
    reason: str


@dataclass
class MHDTimeframeStats:
    timeframe: str
    entry_mode: str
    bars: int
    instruments: int
    signals: int
    trades: int
    wins: int
    win_rate_pct: float
    total_return_pct: float
    avg_return_pct: float
    max_drawdown_pct: float
    profit_factor: float | None
    avg_hold_bars: float
    trades_detail: list[MHDTrade] = field(default_factory=list)
    per_instrument: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self, *, include_trades: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if not include_trades:
            data.pop("trades_detail", None)
        return data


def _db_path() -> Path:
    raw = os.environ.get("DQUANT_ENGINE_DB", "data/engine.db")
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _load_candles(
    con: sqlite3.Connection, inst_id: str, timeframe: str, max_bars: int
) -> list[tuple]:
    rows = con.execute(
        "SELECT ts_ms, open, high, low, close, vol, vol_quote FROM candles "
        "WHERE inst_id=? AND bar=? AND confirmed=1 ORDER BY ts_ms",
        (inst_id, timeframe),
    ).fetchall()
    return rows[-max_bars:]


def _instrument_ids(
    con: sqlite3.Connection, timeframe: str, requested: list[str] | None
) -> list[str]:
    if requested:
        return [item for item in requested if item.endswith("-USDT-SWAP")]
    rows = con.execute(
        "SELECT DISTINCT inst_id FROM candles WHERE bar=? ORDER BY inst_id", (timeframe,)
    ).fetchall()
    return [str(row[0]) for row in rows]


def _simulate_body(
    inst_id: str,
    timeframe: str,
    rows: list[tuple],
    rr_ratio: float = 1.0,
) -> tuple[list[MHDTrade], int]:
    """固定盈亏比的新入场逻辑。

    实心阴线 → 虚心阳线的第一根阳线收盘开多；
    虚心阳线 → 实心阴线的第一根阴线收盘开空。
    止损放在入场 K 线的最低/最高点，止盈为 ``rr_ratio`` R。
    """

    del timeframe
    if rr_ratio <= 0:
        raise ValueError("rr_ratio must be positive")
    if len(rows) < 3:
        return [], 0
    opens = [float(row[1]) for row in rows]
    highs = [float(row[2]) for row in rows]
    lows = [float(row[3]) for row in rows]
    closes = [float(row[4]) for row in rows]
    ts = [int(row[0]) for row in rows]
    body = [
        1 if closes[i] > opens[i] else (-1 if closes[i] < opens[i] else 0)
        for i in range(len(rows))
    ]
    signals = sum(
        1
        for i in range(1, len(rows))
        if body[i] != 0 and body[i - 1] != 0 and body[i] != body[i - 1]
    )
    trades: list[MHDTrade] = []
    index = 1
    while index < len(rows) - 1:
        if body[index] == 0 or body[index - 1] == 0 or body[index] == body[index - 1]:
            index += 1
            continue
        side = "long" if body[index] == 1 else "short"
        entry = closes[index]
        stop = lows[index] if side == "long" else highs[index]
        risk = abs(entry - stop)
        if entry <= 0 or risk <= 0:
            index += 1
            continue
        target = (
            entry + float(rr_ratio) * risk
            if side == "long"
            else entry - float(rr_ratio) * risk
        )
        exit_index = len(rows) - 1
        exit_price = closes[-1]
        reason = "end_of_data"
        for j in range(index + 1, len(rows)):
            if side == "long":
                if lows[j] <= stop:
                    exit_index = j
                    exit_price = min(opens[j], stop)
                    reason = "stop_1r"
                    break
                if highs[j] >= target:
                    exit_index = j
                    exit_price = max(opens[j], target)
                    reason = f"target_{rr_ratio:g}r"
                    break
            else:
                if highs[j] >= stop:
                    exit_index = j
                    exit_price = max(opens[j], stop)
                    reason = "stop_1r"
                    break
                if lows[j] <= target:
                    exit_index = j
                    exit_price = min(opens[j], target)
                    reason = f"target_{rr_ratio:g}r"
                    break
        raw_return = (exit_price - entry) / entry * 100
        if side == "short":
            raw_return = -raw_return
        trades.append(
            MHDTrade(
                inst_id=inst_id,
                side=side,
                entry_ts_ms=ts[index],
                exit_ts_ms=ts[exit_index],
                entry_price=entry,
                exit_price=exit_price,
                return_pct=raw_return,
                bars_held=exit_index - index,
                reason=reason,
            )
        )
        index = exit_index + 1
    return trades, signals


def _simulate_instrument(
    inst_id: str,
    timeframe: str,
    rows: list[tuple],
    config: MHDConfig,
    atr_mult: Decimal,
) -> tuple[list[MHDTrade], int]:
    if len(rows) < config.warmup_bars + 10:
        return [], 0
    opens = [float(row[1]) for row in rows]
    highs = [float(row[2]) for row in rows]
    lows = [float(row[3]) for row in rows]
    closes = [float(row[4]) for row in rows]
    ts = [int(row[0]) for row in rows]
    result = detect_mhd(highs, lows, closes, config)
    signals = sorted(result.signals, key=lambda item: (item.confirmed_index, item.d))
    trades: list[MHDTrade] = []
    last_exit_index = -1
    for signal in signals:
        entry_index = signal.confirmed_index
        if entry_index <= last_exit_index or entry_index >= len(rows) - 1:
            continue
        entry = Decimal(str(closes[entry_index]))
        atr_entry = result.atr[entry_index]
        if not (atr_entry == atr_entry) or atr_entry <= 0:
            continue
        side = "short" if signal.kind == "top" else "long"
        try:
            stop = initial_stop(
                side, entry, Decimal(str(float(atr_entry))), atr_mult
            )
        except ValueError:
            continue
        exit_index = len(rows) - 1
        exit_price = closes[-1]
        reason = "end_of_data"
        for index in range(entry_index + 1, len(rows)):
            atr_now = result.atr[index]
            if not (atr_now == atr_now) or atr_now <= 0:
                atr_now = atr_entry
            atr_decimal = Decimal(str(float(atr_now)))
            if side == "long":
                reference = max(highs[entry_index : index + 1])
                stop = trailing_stop(side, Decimal(str(reference)), atr_decimal, atr_mult, stop)
                if lows[index] <= float(stop):
                    exit_index = index
                    exit_price = min(opens[index], float(stop))
                    reason = "atr_stop"
                    break
            else:
                reference = min(lows[entry_index : index + 1])
                stop = trailing_stop(side, Decimal(str(reference)), atr_decimal, atr_mult, stop)
                if highs[index] >= float(stop):
                    exit_index = index
                    exit_price = max(opens[index], float(stop))
                    reason = "atr_stop"
                    break
        if entry <= 0 or exit_price <= 0:
            continue
        entry_float = float(entry)
        raw_return = (float(exit_price) - entry_float) / entry_float * 100
        if side == "short":
            raw_return = -raw_return
        trades.append(
            MHDTrade(
                inst_id=inst_id,
                side=side,
                entry_ts_ms=ts[entry_index],
                exit_ts_ms=ts[exit_index],
                entry_price=float(entry),
                exit_price=float(exit_price),
                return_pct=float(raw_return),
                bars_held=exit_index - entry_index,
                reason=reason,
            )
        )
        last_exit_index = exit_index
    return trades, len(result.signals)


def _build_stats(
    timeframe: str,
    entry_mode: str,
    trades: list[MHDTrade],
    signals: int,
    bars: int,
    instruments: int,
    per_instrument: dict[str, dict[str, Any]],
) -> MHDTimeframeStats:
    trades_sorted = sorted(trades, key=lambda item: item.entry_ts_ms)
    wins = sum(1 for trade in trades_sorted if trade.return_pct > 0)
    sum_returns = 0.0
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    gross_profit = 0.0
    gross_loss = 0.0
    for trade in trades_sorted:
        r = trade.return_pct / 100.0
        equity *= 1.0 + r
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak * 100)
        if trade.return_pct > 0:
            gross_profit += trade.return_pct
        elif trade.return_pct < 0:
            gross_loss += trade.return_pct
        sum_returns += trade.return_pct
    count = len(trades_sorted)
    total_return = (equity - 1.0) * 100.0 if equity > 0 else -100.0
    profit_factor = (
        gross_profit / abs(gross_loss)
        if gross_loss < 0
        else (999.0 if gross_profit > 0 else None)
    )
    return MHDTimeframeStats(
        timeframe=timeframe,
        entry_mode=entry_mode,
        bars=bars,
        instruments=instruments,
        signals=signals,
        trades=count,
        wins=wins,
        win_rate_pct=(wins / count * 100.0) if count else 0.0,
        total_return_pct=total_return,
        avg_return_pct=(sum_returns / count) if count else 0.0,
        max_drawdown_pct=max_drawdown,
        profit_factor=profit_factor,
        avg_hold_bars=(sum(t.bars_held for t in trades_sorted) / count) if count else 0.0,
        trades_detail=trades_sorted,
        per_instrument=per_instrument,
    )


def run_mhd_backtests(
    *,
    timeframes: list[str] | None = None,
    inst_ids: list[str] | None = None,
    max_bars: int = DEFAULT_MAX_BARS,
    atr_mult: Decimal = DEFAULT_ATR_MULT,
    entry_mode: str = "body",
    rr_ratio: float = 1.0,
    persist: bool = True,
) -> list[MHDTimeframeStats]:
    """对指定周期执行历史回测；body 模式使用实心转虚心 + 固定盈亏比。"""

    if entry_mode not in {"body", "mhd"}:
        raise ValueError("entry_mode must be body or mhd")
    if rr_ratio <= 0:
        raise ValueError("rr_ratio must be positive")

    selected_timeframes = [tf for tf in (timeframes or DEFAULT_TIMEFRAMES)]
    valid = {bar.value for bar in CandleBar}
    selected_timeframes = [tf for tf in selected_timeframes if tf in valid]
    db_path = _db_path()
    con = sqlite3.connect(db_path)
    stats_list: list[MHDTimeframeStats] = []
    try:
        for timeframe in selected_timeframes:
            instruments = _instrument_ids(con, timeframe, inst_ids)
            all_trades: list[MHDTrade] = []
            per_instrument: dict[str, dict[str, Any]] = {}
            total_signals = 0
            total_bars = 0
            for inst_id in instruments:
                rows = _load_candles(con, inst_id, timeframe, max_bars)
                if entry_mode == "body":
                    trades, signal_count = _simulate_body(
                        inst_id, timeframe, rows, rr_ratio=rr_ratio
                    )
                else:
                    trades, signal_count = _simulate_instrument(
                        inst_id, timeframe, rows, MHDConfig(), atr_mult
                    )
                total_signals += signal_count
                total_bars = max(total_bars, len(rows))
                all_trades.extend(trades)
                per_instrument[inst_id] = {
                    "bars": len(rows),
                    "signals": signal_count,
                    "trades": len(trades),
                    "return_pct": sum(t.return_pct for t in trades),
                }
            stats_list.append(
                _build_stats(
                    timeframe,
                    entry_mode,
                    all_trades,
                    total_signals,
                    total_bars,
                    len(instruments),
                    per_instrument,
                )
            )
    finally:
        con.close()

    if persist and stats_list:
        _persist_results(
            stats_list,
            max_bars=max_bars,
            atr_mult=atr_mult,
            entry_mode=entry_mode,
            rr_ratio=rr_ratio,
        )
    return stats_list


def _persist_results(
    stats_list: list[MHDTimeframeStats],
    *,
    max_bars: int,
    atr_mult: Decimal,
    entry_mode: str,
    rr_ratio: float,
) -> None:
    db_path = _db_path()
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(engine, future=True)
    now_ms = int(time.time() * 1000)
    with session_factory() as session:
        for stats in stats_list:
            run_id = f"mhd-{stats.timeframe}-{now_ms}"
            session.merge(
                BacktestRunModel(
                    run_id=run_id,
                    strategy="MHD",
                    params={
                        "timeframe": stats.timeframe,
                        "entry_mode": entry_mode,
                        "rr_ratio": rr_ratio,
                        "max_bars": max_bars,
                        "atr_mult": float(atr_mult),
                    },
                    period=f"last {max_bars} bars",
                    metrics_json={
                        "created_ts_ms": now_ms,
                        "metrics": stats.to_dict(include_trades=stats.timeframe == "1H"),
                    },
                    report_path=None,
                )
            )
        session.commit()
    engine.dispose()


def latest_results() -> list[dict[str, Any]]:
    """返回每个周期最近一次回测记录（按周期去重）。"""

    db_path = _db_path()
    if not db_path.is_file():
        return []
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(engine, future=True)
    latest: dict[str, dict[str, Any]] = {}
    try:
        with session_factory() as session:
            rows = session.execute(
                select(BacktestRunModel)
                .where(BacktestRunModel.strategy == "MHD")
                .order_by(BacktestRunModel.run_id.desc())
            ).scalars()
            for row in rows:
                try:
                    payload = json.loads(json.dumps(row.metrics_json, default=str))
                    metrics = payload.get("metrics", {})
                    timeframe = str(metrics.get("timeframe", ""))
                    if timeframe and timeframe not in latest:
                        latest[timeframe] = {
                            "run_id": row.run_id,
                            "timeframe": timeframe,
                            "created_ts_ms": payload.get("created_ts_ms"),
                            "params": row.params,
                            "metrics": metrics,
                        }
                except Exception:  # noqa: BLE001
                    continue
    finally:
        try:
            session.close()
        except Exception:  # noqa: BLE001
            pass
        engine.dispose()
    order = {tf: i for i, tf in enumerate(DEFAULT_TIMEFRAMES)}
    return sorted(latest.values(), key=lambda item: order.get(item["timeframe"], 99))


def latest_trades(timeframe: str = "1H", entry_mode: str = "body") -> list[dict[str, Any]]:
    """读取某周期、某入场模式最近一次回测保存的逐笔交易。"""

    db_path = _db_path()
    if not db_path.is_file():
        return []
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(engine, future=True)
    try:
        with session_factory() as session:
            rows = session.execute(
                select(BacktestRunModel)
                .where(BacktestRunModel.strategy == "MHD")
                .where(BacktestRunModel.run_id.like(f"mhd-{timeframe}-%"))
                .order_by(BacktestRunModel.run_id.desc())
            ).scalars()
            for row in rows:
                params = row.params if isinstance(row.params, dict) else {}
                if params.get("entry_mode") != entry_mode:
                    continue
                payload = row.metrics_json or {}
                metrics = payload.get("metrics", {}) if isinstance(payload, dict) else {}
                trades = metrics.get("trades_detail")
                if isinstance(trades, list):
                    return trades
    finally:
        engine.dispose()
    return []


__all__ = [
    "DEFAULT_TIMEFRAMES",
    "MHDTimeframeStats",
    "latest_results",
    "latest_trades",
    "run_mhd_backtests",
]


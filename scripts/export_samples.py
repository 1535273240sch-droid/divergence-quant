"""背离样本可视化与 SVG 导出工具（计划书 T3.3）。

从 CSV K 线读取数据，调用 M04 背离检测器，把每条背离信号渲染成 SVG 标注图：
主图 K 线 + 价格极值连线，副图成交量与 MACD 柱（两段参与比较的柱段高亮
并标注面积数值）。SVG 为零第三方依赖实现，可直接在浏览器打开。

用法::

    py scripts/export_samples.py --input tests/fixtures/synthetic_candles_4h.csv \
        --output-dir reports/samples

不连接交易所、不写数据库、不包含任何密钥。
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

# 允许以 `py scripts/export_samples.py` 直接运行。
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import numpy as np  # noqa: E402

from dquant.contracts import Candle, CandleBar, DivergenceSignal  # noqa: E402
from dquant.core.divergence import detect_divergences  # noqa: E402
from dquant.core.indicators import macd as macd_indicator  # noqa: E402

_WIDTH = 900
_HEIGHT = 560
_PRICE_TOP = 20
_PRICE_BOTTOM = 300
_VOL_TOP = 316
_VOL_BOTTOM = 396
_MACD_TOP = 412
_MACD_BOTTOM = 540
_LEFT = 56
_RIGHT = 24
_PLOT_WIDTH = _WIDTH - _LEFT - _RIGHT
_PRELIM_COLOR = "#FFB547"
_UP_COLOR = "#00F5A0"
_DOWN_COLOR = "#FF4D5E"
_TEXT_COLOR = "#CBD5E1"
_GRID_COLOR = "rgba(255,255,255,0.08)"


def parse_candles_csv(path: str | Path) -> list[Candle]:
    """从 CSV 读取 K 线并构造公共契约 Candle。"""

    candles: list[Candle] = []
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"inst_id", "bar", "ts_ms", "open", "high", "low", "close", "volume"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"candle CSV missing columns: {sorted(missing)}")
        for row in reader:
            candles.append(
                Candle(
                    inst_id=row["inst_id"],
                    bar=CandleBar(row["bar"]),
                    ts_ms=int(row["ts_ms"]),
                    open=Decimal(row["open"]),
                    high=Decimal(row["high"]),
                    low=Decimal(row["low"]),
                    close=Decimal(row["close"]),
                    volume=Decimal(row["volume"]),
                    quote_volume=Decimal(row.get("quote_volume") or "0"),
                    confirmed=row.get("confirmed", "true").lower() in ("true", "1"),
                )
            )
    if not candles:
        raise ValueError(f"candle CSV contains no rows: {path}")
    return candles


def _fmt_ts(ts_ms: int) -> str:
    """UTC 时间标签 MM-DD HH:mm。"""

    import datetime as dt

    return dt.datetime.fromtimestamp(ts_ms / 1000, tz=dt.UTC).strftime("%m-%d %H:%M")


def render_sample_svg(
    candles: Sequence[Candle],
    divergences: Sequence[DivergenceSignal],
    inst_id: str = "TEST-USDT-SWAP",
) -> str:
    """渲染一张带背离标注的 SVG 图。

    主图：K 线 + 价格极值连线（``extreme-line``）；
    副图：成交量与 MACD 柱，参与背离的两段柱子以 ``seg-highlight`` 高亮，
    并在确认位置标注类型、评分与两段面积。
    """

    if not candles:
        raise ValueError("candles must not be empty")

    highs = [float(c.high) for c in candles]
    lows = [float(c.low) for c in candles]
    volumes = [float(c.volume) for c in candles]
    max_price = max(highs)
    min_price = min(lows)
    max_vol = max(volumes) if volumes else 1.0

    close_values = np.asarray([float(c.close) for c in candles], dtype=np.float64)
    _dif, _dea, hist = macd_indicator(close_values)
    finite = np.abs(hist[np.isfinite(hist)])
    max_hist = float(finite.max()) if finite.size else 1.0

    count = len(candles)
    slot = _PLOT_WIDTH / max(count, 1)
    ts_index = {candle.ts_ms: index for index, candle in enumerate(candles)}

    def x_of(index: int) -> float:
        return _LEFT + slot * (index + 0.5)

    def y_price(value: float) -> float:
        span = max(max_price - min_price, 1e-9)
        return _PRICE_BOTTOM - (value - min_price) / span * (_PRICE_BOTTOM - _PRICE_TOP)

    def y_volume(value: float) -> float:
        return _VOL_BOTTOM - (value / max_vol if max_vol else 0.0) * (_VOL_BOTTOM - _VOL_TOP)

    mid = (_MACD_TOP + _MACD_BOTTOM) / 2

    def y_macd(value: float) -> float:
        ratio = value / max_hist if max_hist else 0.0
        return mid - ratio * (_MACD_BOTTOM - _MACD_TOP) / 2

    highlight: set[int] = set()
    for signal in divergences:
        start = ts_index.get(signal.seg1_start_ts)
        end = ts_index.get(signal.seg2_end_ts)
        if start is None or end is None:
            continue
        highlight.update(range(start, end + 1))

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_WIDTH}" height="{_HEIGHT}" '
        f'viewBox="0 0 {_WIDTH} {_HEIGHT}">'
    )
    parts.append(f'<rect width="{_WIDTH}" height="{_HEIGHT}" fill="#000000"/>')
    parts.append(
        f'<text x="{_LEFT}" y="14" fill="#FFFFFF" font-size="13" '
        f'font-family="monospace">{inst_id} · 背离样本标注 · {_fmt_ts(candles[0].ts_ms)} ~ '
        f"{_fmt_ts(candles[-1].ts_ms)}</text>"
    )
    for y in (_PRICE_TOP, _PRICE_BOTTOM, _VOL_TOP, _VOL_BOTTOM, _MACD_TOP, _MACD_BOTTOM):
        parts.append(
            f'<line x1="{_LEFT}" y1="{y}" x2="{_WIDTH - _RIGHT}" y2="{y}" '
            f'stroke="{_GRID_COLOR}" stroke-width="1"/>'
        )
    parts.append(
        f'<text x="{_LEFT}" y="{_MACD_TOP - 4}" fill="{_TEXT_COLOR}" font-size="11" '
        f'font-family="monospace">MACD</text>'
    )

    for index, candle in enumerate(candles):
        x = x_of(index)
        body_top = y_price(max(float(candle.open), float(candle.close)))
        body_bottom = y_price(min(float(candle.open), float(candle.close)))
        color = _UP_COLOR if candle.close >= candle.open else _DOWN_COLOR
        parts.append(
            f'<line x1="{x:.2f}" y1="{y_price(float(candle.high)):.2f}" x2="{x:.2f}" '
            f'y2="{y_price(float(candle.low)):.2f}" stroke="{color}" stroke-width="1"/>'
        )
        parts.append(
            f'<rect x="{x - slot * 0.32:.2f}" y="{body_top:.2f}" width="{slot * 0.64:.2f}" '
            f'height="{max(body_bottom - body_top, 1.0):.2f}" fill="{color}"/>'
        )
        bar_height = max(_VOL_BOTTOM - y_volume(volumes[index]), 0.5)
        parts.append(
            f'<rect x="{x - slot * 0.32:.2f}" y="{_VOL_BOTTOM - bar_height:.2f}" '
            f'width="{slot * 0.64:.2f}" height="{bar_height:.2f}" fill="{color}" '
            f'opacity="0.35"/>'
        )

    for index in range(count):
        value = float(hist[index])
        if value != value:  # MACD 预热期 NaN
            continue
        x = x_of(index)
        y = y_macd(value)
        color = _UP_COLOR if value > 0 else _DOWN_COLOR
        opacity = "1.0" if index in highlight else "0.45"
        cls = ' class="seg-highlight"' if index in highlight else ""
        top = min(y, mid)
        height = max(abs(mid - y), 0.5)
        parts.append(
            f'<rect x="{x - slot * 0.32:.2f}" y="{top:.2f}" width="{slot * 0.64:.2f}" '
            f'height="{height:.2f}" fill="{color}" opacity="{opacity}"{cls}/>'
        )

    for signal in divergences:
        is_bull = signal.kind.value.startswith("BULL")
        color = _UP_COLOR if is_bull else _DOWN_COLOR
        i1 = ts_index.get(signal.seg1_extreme_ts)
        i2 = ts_index.get(signal.seg2_extreme_ts)
        if i1 is not None and i2 is not None:
            parts.append(
                f'<line x1="{x_of(i1):.2f}" y1="{y_price(float(signal.seg1_extreme_price)):.2f}" '
                f'x2="{x_of(i2):.2f}" y2="{y_price(float(signal.seg2_extreme_price)):.2f}" '
                f'stroke="{color}" stroke-width="1.5" class="extreme-line"/>'
            )
            for xi, yi in ((i1, signal.seg1_extreme_price), (i2, signal.seg2_extreme_price)):
                parts.append(
                    f'<circle cx="{x_of(xi):.2f}" cy="{y_price(float(yi)):.2f}" r="3" '
                    f'fill="{color}"/>'
                )
        label = f"{signal.kind.value} {signal.score:.0f}"
        stroke = _PRELIM_COLOR if signal.preliminary else color
        if signal.preliminary:
            label += " (早期)"
        i_end = ts_index.get(signal.seg2_end_ts)
        if i_end is not None:
            parts.append(
                f'<text x="{x_of(i_end):.2f}" y="{_MACD_BOTTOM + 16}" fill="{stroke}" '
                f'font-size="10" font-family="monospace" text-anchor="middle">{label}</text>'
            )
            parts.append(
                f'<text x="{x_of(i_end):.2f}" y="{_MACD_BOTTOM + 30}" fill="{_TEXT_COLOR}" '
                f'font-size="9" font-family="monospace" text-anchor="middle">'
                f"面积 {signal.seg1_area:.2f} → {signal.seg2_area:.2f}</text>"
            )

    parts.append("</svg>")
    return "".join(parts)


def main(argv: list[str] | None = None) -> int:
    """命令行入口，返回写入的 SVG 文件数。"""

    parser = argparse.ArgumentParser(description="Export divergence sample SVG charts")
    parser.add_argument(
        "--input",
        default="tests/fixtures/synthetic_candles_4h.csv",
        help="Input CSV path of candles",
    )
    parser.add_argument("--output-dir", default="reports/samples", help="Output directory")
    args = parser.parse_args(argv)

    candles = parse_candles_csv(args.input)
    unconfirmed = [candle for candle in candles if not candle.confirmed]
    if unconfirmed:
        raise ValueError(f"input contains {len(unconfirmed)} unconfirmed candles")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    grouped: dict[str, list[Candle]] = {}
    for candle in candles:
        grouped.setdefault(candle.inst_id, []).append(candle)

    written = 0
    for inst_id, series in grouped.items():
        series.sort(key=lambda item: item.ts_ms)
        for signal in detect_divergences(series):
            svg = render_sample_svg(series, [signal], inst_id=inst_id)
            tag = "early" if signal.preliminary else signal.kind.value.lower()
            name = f"{inst_id}_{tag}_{signal.confirmed_ts}.svg".replace("/", "_")
            (output_dir / name).write_text(svg, encoding="utf-8")
            written += 1
    print(f"written {written} svg files to {output_dir}")
    return written


if __name__ == "__main__":
    main()

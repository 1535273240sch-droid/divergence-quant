"""背离参数网格校准与金标样本评估脚本（计划书 T3.4）。

读取金标样本 CSV 与对应 K 线数据，在 ``area_ratio`` × ``min_segment_bars`` ×
``volume_ratio`` 参数网格上逐一运行 M04 检测器，统计金标召回率与信号精确率，
推荐最优参数并输出 Markdown 调参报告。

金标 CSV 支持两种列格式：

1. 计划书 §5.6.6 人工标注格式：
   ``inst_id,bar,kind,seg1_approx_time,seg2_approx_time,source,note``
   （时间为 ``YYYY-MM-DD HH:MM``，按 UTC 解析）；
2. 机器导出格式（tests/fixtures/golden_divergences.csv）：
   ``inst_id,bar,kind,confirmed_ts,seg1_start_ts,seg2_end_ts``（UTC 毫秒）。

匹配规则：同一交易对、kind 相同，且 seg1 起点与 seg2 终点均在容忍根数内
（默认 2 根）。召回率 = 命中金标数 / 金标总数；精确率 = 命中信号数 / 信号总数。

用法::

    py scripts/calibrate_divergence.py --golden tests/fixtures/golden_divergences.csv \
        --input tests/fixtures/synthetic_candles_4h.csv --output docs/tuning_log.md

赞/踩（divergence_feedback）数据接入需等待 M01 数据库层，见 HANDOFF 已知限制。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import itertools
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# 允许以 `py scripts/calibrate_divergence.py` 直接运行。
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from dquant.contracts import Candle, DivergenceKind  # noqa: E402
from dquant.core.divergence import DivergenceConfig, detect_divergences  # noqa: E402

_DEFAULT_INPUT = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "synthetic_candles_4h.csv"
)
_DEFAULT_TOLERANCE_BARS = 2
_AREA_GRID = (0.5, 0.6, 0.7, 0.8, 0.9)
_MIN_BARS_GRID = (2, 3, 4)
_VOLUME_GRID = (0.7, 0.8, 0.9)
_BAR_DURATION_MS = {"1H": 3_600_000, "4H": 14_400_000, "1D": 86_400_000}


@dataclass(frozen=True, slots=True)
class GoldenSample:
    """一条金标样本（用户人工确认的背离）。"""

    inst_id: str
    bar: str
    kind: str
    seg1_start_ts: int
    seg2_end_ts: int
    source: str = ""
    note: str = ""


class CalibrationReport:
    """校准报告结果。"""

    def __init__(
        self,
        best_params: dict[str, float | int],
        golden_total: int,
        matched_count: int,
        recall: float,
        precision: float,
        signal_count: int = 0,
    ):
        self.best_params = best_params
        self.golden_total = golden_total
        self.matched_count = matched_count
        self.recall = recall
        self.precision = precision
        self.signal_count = signal_count


def _parse_human_time(text: str) -> int:
    """把 ``YYYY-MM-DD HH:MM``（UTC）解析为毫秒时间戳。"""

    moment = dt.datetime.strptime(text.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=dt.UTC)
    return int(moment.timestamp() * 1000)


def load_golden_csv(path: str | Path) -> list[GoldenSample]:
    """读取金标样本 CSV，兼容人工标注与机器导出两种列格式。"""

    samples: list[GoldenSample] = []
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        missing = {"inst_id", "bar", "kind"} - columns
        if missing:
            raise ValueError(f"golden CSV missing columns: {sorted(missing)}")
        machine_style = {"seg1_start_ts", "seg2_end_ts"} <= columns
        human_style = {"seg1_approx_time", "seg2_approx_time"} <= columns
        if not machine_style and not human_style:
            raise ValueError(
                "golden CSV must contain seg1/seg2 columns "
                "(seg1_start_ts+seg2_end_ts or seg1_approx_time+seg2_approx_time)"
            )
        for row in reader:
            if machine_style:
                seg1_ts = int(row["seg1_start_ts"])
                seg2_ts = int(row["seg2_end_ts"])
            else:
                seg1_ts = _parse_human_time(row["seg1_approx_time"])
                seg2_ts = _parse_human_time(row["seg2_approx_time"])
            samples.append(
                GoldenSample(
                    inst_id=row["inst_id"].strip(),
                    bar=row["bar"].strip(),
                    kind=row["kind"].strip(),
                    seg1_start_ts=seg1_ts,
                    seg2_end_ts=seg2_ts,
                    source=row.get("source", "").strip(),
                    note=row.get("note", "").strip(),
                )
            )
    if not samples:
        raise ValueError(f"golden CSV contains no samples: {path}")
    return samples


def load_candles_grouped(path: str | Path) -> dict[str, list[Candle]]:
    """读取 K 线 CSV（export_samples 同格式）并按交易对分组、按时间排序。"""

    import importlib.util

    module_path = Path(__file__).resolve().with_name("export_samples.py")
    spec = importlib.util.spec_from_file_location("export_samples", module_path)
    assert spec is not None and spec.loader is not None
    export = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("export_samples", export)
    spec.loader.exec_module(export)
    grouped: dict[str, list[Candle]] = {}
    for candle in export.parse_candles_csv(path):
        grouped.setdefault(candle.inst_id, []).append(candle)
    for series in grouped.values():
        series.sort(key=lambda item: item.ts_ms)
    return grouped


def evaluate_params(
    grouped: dict[str, list[Candle]],
    samples: Sequence[GoldenSample],
    *,
    area_ratio: float,
    min_segment_bars: int,
    volume_ratio: float,
    tolerance_bars: int = _DEFAULT_TOLERANCE_BARS,
) -> tuple[int, int, int]:
    """用一组参数检测并统计（命中金标数, 信号总数, 命中信号数）。"""

    config = DivergenceConfig(
        area_ratio=area_ratio,
        segment={"min_segment_bars": min_segment_bars, "confirm_bars": 2},
        volume={"enabled": True, "required": False, "volume_ratio": volume_ratio},
    )
    bar_ms = _BAR_DURATION_MS[samples[0].bar]
    tolerance_ms = tolerance_bars * bar_ms

    matched = 0
    hit_signals = 0
    signal_total = 0
    for inst_id, series in grouped.items():
        signals = [
            signal for signal in detect_divergences(series, config) if not signal.preliminary
        ]
        signal_total += len(signals)
        for sample in (item for item in samples if item.inst_id == inst_id):
            for signal in signals:
                if signal.kind != DivergenceKind(sample.kind):
                    continue
                if (
                    abs(signal.seg1_start_ts - sample.seg1_start_ts) <= tolerance_ms
                    and abs(signal.seg2_end_ts - sample.seg2_end_ts) <= tolerance_ms
                ):
                    matched += 1
                    hit_signals += 1
                    break
    return matched, signal_total, hit_signals


def run_calibration(
    golden_path: str | Path,
    input_path: str | Path = _DEFAULT_INPUT,
) -> CalibrationReport:
    """执行网格搜索：召回率优先，其次精确率，再次信号数更少（更保守）。"""

    samples = load_golden_csv(golden_path)
    grouped = load_candles_grouped(input_path)

    best: tuple[tuple[float, float, int], float, int, int, int, dict] | None = None
    for area, min_bars, volume in itertools.product(_AREA_GRID, _MIN_BARS_GRID, _VOLUME_GRID):
        matched, total, hits = evaluate_params(
            grouped,
            samples,
            area_ratio=area,
            min_segment_bars=min_bars,
            volume_ratio=volume,
        )
        recall = matched / len(samples)
        precision = hits / total if total else 0.0
        rank = (recall, precision, -total)
        if best is None or rank > best[0]:
            best = (
                rank,
                recall,
                precision,
                matched,
                total,
                hits,
                {
                    "area_ratio": area,
                    "min_segment_bars": min_bars,
                    "volume_ratio": volume,
                },
            )
    assert best is not None
    _rank, recall, precision, matched, total, hits, params = best
    return CalibrationReport(
        best_params=params,
        golden_total=len(samples),
        matched_count=matched,
        recall=recall,
        precision=precision,
        signal_count=total,
    )


def render_report(report: CalibrationReport, golden_path: str | Path) -> str:
    """把校准结论渲染为 Markdown。"""

    assert report.best_params is not None
    return (
        "# 背离参数校准与金标测试报告\n"
        "\n"
        "## 1. 评估概要\n"
        f"- **金标样本总数**: {report.golden_total}（{Path(golden_path).name}）\n"
        f"- **成功匹配数**: {report.matched_count}\n"
        f"- **召回率**: {report.recall:.2%}（计划书要求 ≥ 80%）\n"
        f"- **精确率**: {report.precision:.2%}\n"
        f"- **信号总数**: {report.signal_count}\n"
        "\n"
        "## 2. 最优参数推荐\n"
        f"- `area_ratio={report.best_params['area_ratio']}`\n"
        f"- `min_segment_bars={report.best_params['min_segment_bars']}`\n"
        f"- `volume_ratio={report.best_params['volume_ratio']}`\n"
        "\n"
        "## 3. 结论\n"
        f"当前背离算法在金标数据集上召回率 **{report.recall:.1%}**，"
        "参数建议需经人工评审后写入配置。\n"
        "\n"
        "> 赞/踩（divergence_feedback）样本接入需等待 M01 数据库层，"
        "当前版本仅基于金标样本评估（见 M04 HANDOFF 已知限制）。\n"
    )


def main(argv: list[str] | None = None) -> CalibrationReport:
    """命令行主入口。"""

    parser = argparse.ArgumentParser(
        description="Calibrate divergence parameters against golden dataset"
    )
    parser.add_argument("--golden", required=True, help="Path to golden divergences CSV")
    parser.add_argument(
        "--input",
        default=str(_DEFAULT_INPUT),
        help="Path to candle CSV matching the golden samples",
    )
    parser.add_argument("--output", required=True, help="Path to write tuning report markdown")
    args = parser.parse_args(argv)

    report = run_calibration(args.golden, args.input)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_report(report, args.golden), encoding="utf-8")
    print(
        f"best params: {report.best_params}, "
        f"recall={report.recall:.0%}, precision={report.precision:.0%}, "
        f"report={output_path}"
    )
    return report


if __name__ == "__main__":
    main()

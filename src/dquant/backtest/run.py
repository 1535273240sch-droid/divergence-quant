"""回测 CLI 入口 (docker-compose tools profile: `python -m dquant.backtest.run`)。

当前状态: 骨架入口。参数化全功能回测请参考 tests/unit/test_backtest.py
与 tests/integration/test_backtest_consistency.py 中 BacktestEngine 的组装方式:
    data -> normalize_market_data -> BacktestEngine(strategy=..., risk_gate=...)
后续计划: 从 SQLite/Postgres 装载 candles, 按 config/config.backtest.yaml 跑
S1/S2/S3 对比并输出 HTML 报告 (见 更新说明.md 的整合待办)。
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dquant.backtest.run",
        description="DivergenceQuant 回测运行器 (整合骨架, 尚未接入数据装载)",
    )
    parser.add_argument("--db", default="engine.db", help="SQLite 数据库路径")
    parser.add_argument("--config", default="config/config.backtest.yaml")
    parser.add_argument("--strategy", choices=["S1", "S2", "S3", "ALL"], default="ALL")
    args = parser.parse_args(argv)
    print(
        "dquant.backtest.run: 回测数据装载与报告生成尚未接线 "
        f"(db={args.db}, config={args.config}, strategy={args.strategy})。\n"
        "临时方案: 参照 tests/integration/test_backtest_consistency.py 直接调用 BacktestEngine。"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())

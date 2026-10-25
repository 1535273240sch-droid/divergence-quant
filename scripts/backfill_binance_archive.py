"""Binance 公开历史归档补数脚本（T1.5，长周期回测补充源）。

用法示例（在项目根目录）：

    py scripts/backfill_binance_archive.py --symbols BTCUSDT,ETHUSDT \
        --start 2019-01 --end 2026-09 --out-dir data/binance_archive --dry-run

说明：

- 数据来自 Binance 公开数据仓库（data.binance.vision），是公开历史数据，不需要密钥；
- 从 1h 归档重聚合为 UTC+8 日线（北京时间 00:00 开盘），与 OKX ``bar=1D`` 对齐；
- 默认输出 JSONL 文件（每行一个 ``Candle`` JSON 对象），供回测模块读取；
- ``--write-db`` 时必须显式提供 ``--database-url``，并按 ``--inst-id-suffix`` 把
  ``BTCUSDT`` 映射为 ``BTC-USDT-SWAP`` 这样的 OKX 交易对编号。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

# 让脚本在没有安装包的环境下也能找到 src/dquant（pyproject 的 pythonpath 只对 pytest 生效）。
_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from dquant.data import (  # noqa: E402
    build_archive_url,
    collect_symbol_daily,
    list_month_range,
    upsert_candles,
)
from dquant.db import Base  # noqa: E402

DEFAULT_INST_ID_SUFFIX = "-USDT-SWAP"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Binance 归档日线补数（长周期回测专用）")
    parser.add_argument(
        "--symbols", required=True, help="逗号分隔的 Binance 现货符号，例如 BTCUSDT"
    )
    parser.add_argument("--start", required=True, help="起始月份 YYYY-MM")
    parser.add_argument("--end", required=True, help="结束月份 YYYY-MM")
    parser.add_argument("--out-dir", required=True, help="JSONL 输出目录")
    parser.add_argument(
        "--inst-id-suffix", default=DEFAULT_INST_ID_SUFFIX, help="映射 OKX 交易对的固定后缀"
    )
    parser.add_argument(
        "--write-db", action="store_true", help="同时写入数据库（需要 --database-url）"
    )
    parser.add_argument("--database-url", default=None, help="同步 SQLAlchemy 数据库 URL")
    parser.add_argument("--dry-run", action="store_true", help="只打印将下载的归档清单")
    return parser


def _default_fetcher(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def symbol_to_inst_id(symbol: str, suffix: str) -> str:
    """把 ``BTCUSDT`` 映射为 OKX 交易对编号 ``BTC-USDT-SWAP``；其他符号原样加后缀。"""

    if symbol.endswith("USDT") and len(symbol) > 4:
        return f"{symbol[:-4]}-{suffix}"
    return f"{symbol}{suffix}"


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.write_db and not args.database_url:
        print("--write-db 需要显式提供 --database-url", file=sys.stderr)
        return 2

    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]

    if args.dry_run:
        for symbol in symbols:
            for month in list_month_range(args.start, args.end):
                print(build_archive_url(symbol, "1h", month))
        return 0

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    session_factory = None
    engine = None
    if args.write_db:
        engine = create_engine(args.database_url)
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(engine)

    failures = 0
    try:
        for symbol in symbols:
            inst_id = symbol_to_inst_id(symbol, args.inst_id_suffix)
            try:
                candles = collect_symbol_daily(
                    _default_fetcher, symbol, inst_id, args.start, args.end
                )
            except Exception as exc:
                print(f"{symbol} 归档拉取或聚合失败: {exc}", file=sys.stderr)
                failures += 1
                continue
            output_path = out_dir / f"{inst_id}_1D.jsonl"
            with output_path.open("w", encoding="utf-8", newline="\n") as handle:
                for candle in candles:
                    handle.write(
                        json.dumps(candle.model_dump(mode="json"), ensure_ascii=False) + "\n"
                    )
            print(f"{symbol} -> {output_path} 共 {len(candles)} 根日线")
            if session_factory is not None:
                with session_factory() as session:
                    upserted = upsert_candles(session, candles)
                    session.commit()
                print(f"{inst_id} 写入数据库 {upserted} 行")
    finally:
        if engine is not None:
            engine.dispose()

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

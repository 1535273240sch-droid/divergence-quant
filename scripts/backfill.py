"""OKX 历史补数脚本（T1.5，OKX 主源）。

用法示例（在项目根目录）：

    py scripts/backfill.py --inst-ids BTC-USDT-SWAP --bars 1D,4H,1H \
        --start 2024-01-01 --database-url sqlite:///./backfill.db

说明：

- 只使用 OKX 公开行情接口（不需要 API Key），适配器强制 demo 模式，不连接实盘；
- ``--database-url`` 必须显式提供，脚本不读取 ``config/config.yaml`` 里的数据库地址，
  避免误写生产库；首次运行会按当前 ORM 元数据建表（生产库请先执行 alembic 迁移）；
- 补数写入按主键覆盖，重复执行安全。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 让脚本在没有安装包的环境下也能找到 src/dquant（pyproject 的 pythonpath 只对 pytest 生效）。
_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from dquant.adapters import OkxAdapter, UrllibTransport  # noqa: E402
from dquant.contracts import CandleBar  # noqa: E402
from dquant.data import (  # noqa: E402
    AsyncAdapterRunner,
    backfill_history,
    beijing_date_to_ms,
    validate_bar_alignment,
)
from dquant.db import Base  # noqa: E402
from dquant.settings import load_settings  # noqa: E402

OKX_PUBLIC_BASE_URL = "https://www.okx.com"
DAY_MS = 86_400_000


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OKX 历史 K 线补数（仅公开接口、仅模拟盘）")
    parser.add_argument(
        "--inst-ids", required=True, help="逗号分隔的交易对，例如 BTC-USDT-SWAP,ETH-USDT-SWAP"
    )
    parser.add_argument("--bars", default="1D,4H,1H", help="逗号分隔的周期，默认 1D,4H,1H")
    parser.add_argument("--start", required=True, help="起始日期（北京时间 YYYY-MM-DD）")
    parser.add_argument("--end", default=None, help="结束日期（北京时间 YYYY-MM-DD），缺省补到最新")
    parser.add_argument("--max-pages", type=int, default=200, help="每个交易对的最大翻页数")
    parser.add_argument(
        "--database-url", default=None, help="同步 SQLAlchemy 数据库 URL，写入时必填"
    )
    parser.add_argument(
        "--config", default="config/config.yaml", help="配置文件路径（只用于校验模式）"
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印补数计划，不做网络与写库")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    settings = load_settings(args.config)
    if settings.exchange.mode != "demo":
        print(
            f"配置的 exchange.mode={settings.exchange.mode} 不是 demo，本脚本拒绝执行",
            file=sys.stderr,
        )
        return 2

    try:
        inst_ids = [item.strip() for item in args.inst_ids.split(",") if item.strip()]
        bars = [CandleBar(item.strip()) for item in args.bars.split(",") if item.strip()]
        start_ms = beijing_date_to_ms(args.start)
        end_ms = beijing_date_to_ms(args.end) + DAY_MS - 1 if args.end else None
    except (KeyError, ValueError) as exc:
        print(f"参数错误: {exc}", file=sys.stderr)
        return 2

    for inst_id in inst_ids:
        for bar in bars:
            print(f"计划补数: {inst_id} {bar.value} 从北京时间 {args.start} 开始")

    if args.dry_run:
        return 0

    if not args.database_url:
        print("缺少 --database-url，真实补数必须显式指定写入数据库", file=sys.stderr)
        return 2

    engine = create_engine(args.database_url)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(engine)

    transport = UrllibTransport(OKX_PUBLIC_BASE_URL, None, simulated_trading=True)
    adapter = OkxAdapter(transport, mode=settings.exchange.mode)
    failures = 0
    try:
        with AsyncAdapterRunner(adapter) as source, session_factory() as session:
            try:
                transport.clock_offset_ms = source.sync_time()
            except Exception as exc:  # 时间校准失败不阻塞补数，签名仅在私有接口需要
                print(f"服务器时间校准失败（继续补数）: {exc}", file=sys.stderr)

            for inst_id in inst_ids:
                for bar in bars:
                    if bar is CandleBar.ONE_DAY:
                        validate_bar_alignment(bar, start_ms)
                    stats = backfill_history(
                        source,
                        session,
                        inst_id,
                        bar,
                        start_ms,
                        end_ms,
                        max_pages=args.max_pages,
                    )
                    session.commit()
                    print(
                        f"{inst_id} {bar.value}: 拉取 {stats.fetched_rows} 行，"
                        f"写入 {stats.upserted_rows} 行，剩余缺口 {len(stats.missing_windows)} 个，"
                        f"异常 K 线 {len(stats.anomalies)} 根"
                    )
                    if stats.missing_windows:
                        print(
                            f"  缺口区间（UTC 毫秒）: {stats.missing_windows[:10]}",
                            file=sys.stderr,
                        )
                        failures += 1
    finally:
        engine.dispose()

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

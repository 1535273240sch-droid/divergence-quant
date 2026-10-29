"""为桌面发布包预创建 SQLite 数据库。

构建时运行一次，使用项目自身的 ORM / SQLite 建表逻辑生成：

- engine.db   行情、信号、风控、选币等引擎数据表
- users.db    本地多用户账户表
- factors.db  因子实验室数据表

生成的是空库（不含任何用户密钥或个人数据），首次启动直接使用包内
``data/`` 目录，运行逻辑与源码启动完全一致。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _prepare_env(data_dir: Path, root: Path) -> None:
    os.environ["DQUANT_ENGINE_DB"] = str(data_dir / "engine.db")
    os.environ["DQUANT_USERS_DB"] = str(data_dir / "users.db")
    os.environ["DQUANT_FACTORS_DB"] = str(data_dir / "factors.db")
    os.environ["DQUANT_CREDENTIALS_FILE"] = str(data_dir / "okx-credentials.enc")
    os.environ.setdefault("DQUANT_CONFIG_PATH", str(root / "config" / "config.yaml"))
    src_dir = str(root / "src")
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)


def _checkpoint_and_close(conn, db_path: Path) -> None:
    try:
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
    finally:
        conn.close()
    for suffix in ("-wal", "-shm"):
        leftover = db_path.with_name(db_path.name + suffix)
        if leftover.exists():
            leftover.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化 DivergenceQuant 桌面版空数据库")
    parser.add_argument("--data-dir", required=True, help="发布包 data/ 目录")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    data_dir = Path(args.data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    _prepare_env(data_dir, root)

    from dquant.api.factors import _connect as connect_factors
    from dquant.api.users import _connect as connect_users
    from dquant.scheduler.engine import EngineOptions, TradingEngine

    print(f">>> 初始化 SQLite 数据库目录: {data_dir}")
    engine = TradingEngine(
        EngineOptions(
            source="synthetic",
            dry_run=True,
            db_path=str(data_dir / "engine.db"),
            interval_sec=30.0,
            config_path=str(root / "config" / "config.yaml"),
        )
    )
    engine._sync_engine.dispose()  # type: ignore[attr-defined]

    for name, connector in (("users.db", connect_users), ("factors.db", connect_factors)):
        conn = connector()
        _checkpoint_and_close(conn, data_dir / name)

    expected = ("engine.db", "users.db", "factors.db")
    missing = [name for name in expected if not (data_dir / name).is_file()]
    if missing:
        print(f"[失败] 缺少数据库文件: {', '.join(missing)}", file=sys.stderr)
        return 1

    for name in expected:
        print(f"    - {name}: {(data_dir / name).stat().st_size} bytes")
    print("[成功] 桌面版空数据库已生成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

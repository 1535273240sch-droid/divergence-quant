"""实时数据桥：把引擎 SQLite 与 OKX 公共接口供给 API 层。

设计原则：

1. **只读**：引擎库以 SQLite ``mode=ro`` 打开，绝不干扰 engine 进程写入。
2. **降级优先**：库缺失、表为空、OKX 不可达时返回空集或 ``None``，
   由路由层决定回退到内置演示数据，绝不让接口 500。
3. **不引入 ORM**：这里只做只读投影，直接用 sqlite3 读，避免与引擎的
   异步 Session 生命周期纠缠。
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from dquant.adapters.okx import UrllibTransport

_DEFAULT_DB = "data/engine.db"
_OKX_TTL_SEC = 2.0
_cache: dict[str, tuple[float, Any]] = {}
_OKX_BASE = "https://www.okx.com"
_TIMEOUT_SEC = 8.0


# --------------------------------------------------------------------------
# 引擎库访问
# --------------------------------------------------------------------------
def engine_db_path() -> Path:
    """引擎 SQLite 路径，默认相对当前工作目录的 ``data/engine.db``。"""

    raw = (os.getenv("DQUANT_ENGINE_DB") or _DEFAULT_DB).strip() or _DEFAULT_DB
    path = Path(raw)
    if path.is_absolute():
        return path
    cwd_path = Path.cwd() / path
    if cwd_path.is_file():
        return cwd_path
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    root_candidate = repo_root / path
    if root_candidate.is_file():
        return root_candidate
    return cwd_path


def ensure_engine_db(path: Path | None = None) -> Path:
    """确保引擎 SQLite 数据库文件存在并初始化全部表结构。"""

    target = path or engine_db_path()
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        # 如果旧版根目录存在 engine.db，则优先迁移复制
        legacy_db = Path.cwd() / "engine.db"
        if legacy_db.is_file() and legacy_db.resolve() != target.resolve():
            import shutil
            try:
                shutil.copy2(legacy_db, target)
            except OSError:
                pass
        try:
            from sqlalchemy import create_engine
            from dquant.db.models import Base

            engine = create_engine(f"sqlite:///{target.as_posix()}", future=True)
            Base.metadata.create_all(engine)
            engine.dispose()
        except Exception:
            pass
    return target


def _connect() -> sqlite3.Connection | None:
    path = engine_db_path()
    if not path.is_file():
        ensure_engine_db(path)
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=3.0)
    except sqlite3.Error:
        try:
            conn = sqlite3.connect(str(path), timeout=3.0)
        except sqlite3.Error:
            return None
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _rows(sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    conn = _connect()
    if conn is None:
        return []
    try:
        return list(conn.execute(sql, params).fetchall())
    except sqlite3.Error:
        return []
    finally:
        conn.close()


# --------------------------------------------------------------------------
# 选币 / K 线 / 信号 / 持仓
# --------------------------------------------------------------------------
def latest_run_id() -> str | None:
    rows = _rows("SELECT run_id FROM screener_runs ORDER BY started_at DESC LIMIT 1")
    return str(rows[0]["run_id"]) if rows else None


def candidates(limit: int = 30) -> list[dict[str, Any]]:
    """最新一轮选币结果，按 rank 升序。"""

    run_id = latest_run_id()
    if run_id is None:
        return []
    rows = _rows(
        "SELECT run_id, inst_id, pool, regime, divergence_id, score, rank "
        "FROM screener_results WHERE run_id=? ORDER BY rank ASC LIMIT ?",
        (run_id, limit),
    )
    now_ms = int(time.time() * 1000)
    return [
        {
            "inst_id": r["inst_id"],
            "pool": r["pool"],
            "regime": r["regime"],
            "divergence_id": r["divergence_id"],
            "divergence_kind": None,
            "score": float(r["score"]),
            "rank": int(r["rank"]),
            "updated_ts_ms": now_ms,
        }
        for r in rows
    ]


def candles(inst_id: str, bar: str, limit: int = 280) -> list[dict[str, Any]]:
    """从引擎库取最近 ``limit`` 根已确认 K 线，按时间升序返回。"""

    rows = _rows(
        "SELECT ts_ms, open, high, low, close, vol, vol_quote FROM candles "
        "WHERE inst_id=? AND bar=? AND confirmed=1 ORDER BY ts_ms DESC LIMIT ?",
        (inst_id, bar, limit),
    )
    if not rows:
        return []
    return [
        {
            "ts_ms": int(r["ts_ms"]),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "vol": float(r["vol"] or 0.0),
            "vol_quote": float(r["vol_quote"] or 0.0),
        }
        for r in reversed(rows)
    ]


def pending_signal(signal_id: str) -> dict[str, Any] | None:
    """读取一个仍处于 PENDING 状态的引擎信号。"""

    rows = _rows(
        "SELECT id, strategy, inst_id, side, action, price, stop_loss, "
        "take_profits, reason, status, created_at FROM strategy_signals "
        "WHERE id=? AND status='PENDING' LIMIT 1",
        (signal_id,),
    )
    if not rows:
        return None
    row = rows[0]
    tps_raw = row["take_profits"]
    try:
        import json as _json

        tps = _json.loads(tps_raw) if isinstance(tps_raw, str) else (tps_raw or [])
    except (ValueError, TypeError):
        tps = []
    return {
        "signal_id": str(row["id"]),
        "inst_id": row["inst_id"],
        "side": row["side"],
        "action": row["action"],
        "price": float(row["price"]),
        "stop_loss": float(row["stop_loss"]) if row["stop_loss"] is not None else None,
        "take_profits": tps,
        "reason": row["reason"],
        "strategy_name": row["strategy"],
        "created_at": row["created_at"],
    }


def update_signal_status(signal_id: str, status: str) -> bool:
    """把引擎信号推进到已确认/已拒绝状态。"""

    path = engine_db_path()
    if not path.is_file():
        ensure_engine_db(path)
    if not path.is_file():
        return False
    try:
        conn = sqlite3.connect(str(path), timeout=3.0)
        try:
            cur = conn.execute(
                "UPDATE strategy_signals SET status=? WHERE id=? AND status='PENDING'",
                (status, signal_id),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def available_instruments(bar: str = "4H") -> list[str]:
    rows = _rows(
        "SELECT inst_id, COUNT(*) AS n FROM candles WHERE bar=? "
        "GROUP BY inst_id HAVING n >= 50 ORDER BY inst_id",
        (bar,),
    )
    return [str(r["inst_id"]) for r in rows]


def pending_signals(limit: int = 20) -> list[dict[str, Any]]:
    """引擎落库的待确认信号（status=PENDING）。"""

    rows = _rows(
        "SELECT id, strategy, inst_id, side, action, price, stop_loss, "
        "take_profits, reason, status, created_at FROM strategy_signals "
        "WHERE status='PENDING' ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        tps_raw = r["take_profits"]
        try:
            import json as _json

            tps = _json.loads(tps_raw) if isinstance(tps_raw, str) else (tps_raw or [])
        except (ValueError, TypeError):
            tps = []
        out.append(
            {
                "signal_id": str(r["id"]),
                "inst_id": r["inst_id"],
                "side": r["side"],
                "action": r["action"],
                "price": float(r["price"]),
                "stop_loss": float(r["stop_loss"]) if r["stop_loss"] is not None else None,
                "take_profits": tps,
                "reason": r["reason"],
                "strategy_name": r["strategy"],
                "created_at": r["created_at"],
            }
        )
    return out


def open_positions() -> list[dict[str, Any]]:
    rows = _rows(
        "SELECT inst_id, pos_side, size, avg_px, upl, stop_loss, strategy, opened_at "
        "FROM positions ORDER BY opened_at DESC"
    )
    return [
        {
            "inst_id": r["inst_id"],
            "pos_side": r["pos_side"],
            "size": float(r["size"]),
            "avg_px": float(r["avg_px"]),
            "upl": float(r["upl"]),
            "stop_loss": float(r["stop_loss"]) if r["stop_loss"] is not None else None,
            "strategy": r["strategy"],
            "opened_at": r["opened_at"],
        }
        for r in rows
    ]


def overview() -> dict[str, Any]:
    """引擎运行概览：数据新鲜度、池子规模、信号与订单计数。"""

    path = engine_db_path()
    exists = path.is_file()
    db_mtime = int(path.stat().st_mtime * 1000) if exists else None

    def count(table: str) -> int:
        rows = _rows(f"SELECT COUNT(*) AS n FROM {table}")  # noqa: S608 - 表名来自白名单
        return int(rows[0]["n"]) if rows else 0

    last_candle = _rows("SELECT MAX(ts_ms) AS ts FROM candles")
    run = _rows(
        "SELECT run_id, started_at, finished_at, universe_size FROM screener_runs "
        "ORDER BY started_at DESC LIMIT 1"
    )
    instruments = available_instruments("4H")
    return {
        "engine_db": str(path),
        "engine_db_exists": exists,
        "engine_db_mtime_ms": db_mtime,
        "last_candle_ts_ms": (
            int(last_candle[0]["ts"])
            if last_candle and last_candle[0]["ts"]
            else None
        ),
        "instruments": len(instruments),
        "instrument_list": instruments,
        "candles": count("candles") if exists else 0,
        "screener_results": count("screener_results") if exists else 0,
        "signals": count("strategy_signals") if exists else 0,
        "pending_signals": len(pending_signals()),
        "orders": count("orders") if exists else 0,
        "positions": count("positions") if exists else 0,
        "last_run": {
            "run_id": run[0]["run_id"],
            "started_at": run[0]["started_at"],
            "finished_at": run[0]["finished_at"],
            "universe_size": int(run[0]["universe_size"]),
        }
        if run
        else None,
        "server_ts_ms": int(time.time() * 1000),
    }


# --------------------------------------------------------------------------
# OKX 公共接口（实时盘口 / 成交 / Ticker）
# --------------------------------------------------------------------------
async def _okx_get(path: str, params: dict[str, str]) -> list[dict[str, Any]]:
    """带 2 秒内存缓存的 OKX 公共请求（前端 4~6 秒轮询，无需每次都打代理）。"""

    key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < _OKX_TTL_SEC:
        return hit[1]

    transport = UrllibTransport(_OKX_BASE, None, simulated_trading=True, timeout_sec=_TIMEOUT_SEC)
    payload = await transport.request("GET", path, params, auth=False)
    if not isinstance(payload, dict) or str(payload.get("code")) != "0":
        return []
    data = payload.get("data")
    rows = data if isinstance(data, list) else []
    _cache[key] = (now, rows)
    return rows


async def order_book(inst_id: str, depth: int = 20) -> dict[str, Any]:
    rows = await _okx_get("/api/v5/market/books", {"instId": inst_id, "sz": str(depth)})
    if not rows:
        return {"inst_id": inst_id, "asks": [], "bids": [], "ts_ms": None}
    book = rows[0]
    return {
        "inst_id": inst_id,
        "ts_ms": int(book.get("ts") or 0),
        "asks": [[float(p), float(s)] for p, s, *_ in book.get("asks", [])],
        "bids": [[float(p), float(s)] for p, s, *_ in book.get("bids", [])],
    }


async def recent_trades(inst_id: str, limit: int = 30) -> list[dict[str, Any]]:
    rows = await _okx_get("/api/v5/market/trades", {"instId": inst_id, "limit": str(limit)})
    out = []
    for r in rows:
        try:
            out.append(
                {
                    "trade_id": str(r.get("tradeId")),
                    "price": float(r["px"]),
                    "size": float(r["sz"]),
                    "side": str(r.get("side") or "buy"),
                    "ts_ms": int(r.get("ts") or 0),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


async def ticker(inst_id: str) -> dict[str, Any] | None:
    rows = await _okx_get("/api/v5/market/ticker", {"instId": inst_id})
    if not rows:
        return None
    t = rows[0]
    try:
        return {
            "inst_id": inst_id,
            "last": float(t["last"]),
            "open24h": float(t.get("open24h") or 0.0),
            "high24h": float(t.get("high24h") or 0.0),
            "low24h": float(t.get("low24h") or 0.0),
            "vol24h": float(t.get("vol24h") or 0.0),
            "volCcy24h": float(t.get("volCcy24h") or 0.0),
            "ts_ms": int(t.get("ts") or 0),
        }
    except (KeyError, TypeError, ValueError):
        return None


async def funding_rate(inst_id: str) -> float | None:
    rows = await _okx_get("/api/v5/public/funding-rate", {"instId": inst_id})
    if not rows:
        return None
    try:
        return float(rows[0]["fundingRate"])
    except (KeyError, TypeError, ValueError):
        return None

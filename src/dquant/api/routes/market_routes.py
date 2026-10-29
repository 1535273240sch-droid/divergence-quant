"""市场数据路由：选币候选池、K 线、盘口、成交、持仓。

数据来源优先级：

1. 引擎 SQLite（``data/engine.db``）—— 由 ``dquant.scheduler.engine`` 采集的真实行情；
2. OKX 公共 REST —— 盘口 / 逐笔 / Ticker 这类高频数据不落库，实时转发；
3. 内置演示数据 —— 仅当引擎库缺失或为空时兜底，保证前端不空白。
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Query

from dquant.adapters.errors import ExchangeError
from dquant.api import live_data
from dquant.api.auth import OptionalUser
from dquant.api.errors import APIException
from dquant.contracts.models import Position, ScreenerCandidate
from dquant.contracts.types import PositionSide, RegimeState

router = APIRouter(prefix="/api/v1/market", tags=["行情与持仓"])

# 兜底演示数据：引擎库不可用时才返回，避免前端空白页
_FALLBACK_CANDIDATES: list[dict[str, Any]] = [
    {
        "inst_id": "BTC-USDT-SWAP",
        "pool": "A",
        "regime": "UPTREND",
        "divergence_id": None,
        "divergence_kind": None,
        "score": 92.5,
        "rank": 1,
        "updated_ts_ms": int(time.time() * 1000),
    },
]

_ALLOWED_BARS = {"5m", "15m", "1H", "4H", "1D"}


@router.get("/candidates", response_model=list[ScreenerCandidate], summary="获取多因子选币候选池")
async def list_candidates(user: OptionalUser) -> list[ScreenerCandidate]:
    """返回引擎最新一轮选币结果；无数据时回退演示数据。"""

    del user
    raw = live_data.candidates()
    if not raw:
        raw = _FALLBACK_CANDIDATES
    return [
        ScreenerCandidate(
            inst_id=c["inst_id"],
            pool=c["pool"],
            regime=RegimeState(c["regime"]),
            divergence_id=c.get("divergence_id"),
            divergence_kind=None,
            score=c["score"],
            rank=c["rank"],
            updated_ts_ms=c["updated_ts_ms"],
        )
        for c in raw
    ]


@router.get("/positions", response_model=list[Position], summary="获取当前持仓列表")
async def list_positions(user: OptionalUser) -> list[Position]:
    """返回引擎落库的活动持仓（dry-run 模式下为空）。"""

    del user
    out: list[Position] = []
    for p in live_data.open_positions():
        try:
            out.append(
                Position(
                    inst_id=p["inst_id"],
                    pos_side=PositionSide(p["pos_side"]),
                    size=Decimal(str(p["size"])),
                    avg_price=Decimal(str(p["avg_px"])),
                    mark_price=Decimal(str(p["avg_px"])),
                    upl=Decimal(str(p["upl"])),
                    stop_loss=Decimal(str(p["stop_loss"])) if p["stop_loss"] is not None else None,
                    strategy=p["strategy"],
                    opened_ts_ms=int(time.time() * 1000),
                )
            )
        except (ValueError, TypeError):
            continue
    return out


@router.get("/candles", summary="获取真实 K 线（引擎库）")
async def get_candles(
    user: OptionalUser,
    inst_id: str = Query("BTC-USDT-SWAP", description="合约 ID，如 BTC-USDT-SWAP"),
    bar: str = Query("4H", description="周期：15m / 1H / 4H / 1D"),
    limit: int = Query(280, ge=20, le=1000),
) -> dict[str, Any]:
    """从引擎 SQLite 读取真实 K 线，供前端主图渲染。"""

    del user
    if bar not in _ALLOWED_BARS:
        raise APIException("INVALID_PARAM", f"不支持的周期: {bar}", 400)
    rows = live_data.candles(inst_id, bar, limit)
    return {
        "inst_id": inst_id,
        "bar": bar,
        "count": len(rows),
        "source": "engine-db" if rows else "empty",
        "candles": rows,
    }


@router.get("/universe", summary="获取引擎已采集合约列表")
async def get_universe(user: OptionalUser, bar: str = Query("4H")) -> dict[str, Any]:
    """返回引擎库中有足够历史 K 线的合约，供前端标的切换。"""

    del user
    items = live_data.available_instruments(bar)
    return {"bar": bar, "count": len(items), "instruments": items}


@router.get("/ticker", summary="实时 Ticker（OKX 公共接口）")
async def get_ticker(user: OptionalUser, inst_id: str = Query("BTC-USDT-SWAP")) -> dict[str, Any]:
    del user
    try:
        data = await live_data.ticker(inst_id)
        funding = await live_data.funding_rate(inst_id)
    except ExchangeError as exc:
        raise APIException("EXCHANGE_UNAVAILABLE", f"OKX 行情获取失败: {exc.msg}", 502) from exc
    if data is None:
        raise APIException("NOT_FOUND", f"未找到合约 {inst_id} 的行情", 404)
    data["funding_rate"] = funding
    return data


@router.get("/depth", summary="实时盘口（OKX 公共接口）")
async def get_depth(
    user: OptionalUser,
    inst_id: str = Query("BTC-USDT-SWAP"),
    sz: int = Query(20, ge=5, le=400),
) -> dict[str, Any]:
    del user
    try:
        return await live_data.order_book(inst_id, sz)
    except ExchangeError as exc:
        raise APIException("EXCHANGE_UNAVAILABLE", f"OKX 盘口获取失败: {exc.msg}", 502) from exc


@router.get("/trades", summary="实时逐笔成交（OKX 公共接口）")
async def get_trades(
    user: OptionalUser,
    inst_id: str = Query("BTC-USDT-SWAP"),
    limit: int = Query(30, ge=5, le=100),
) -> dict[str, Any]:
    del user
    try:
        rows = await live_data.recent_trades(inst_id, limit)
    except ExchangeError as exc:
        raise APIException("EXCHANGE_UNAVAILABLE", f"OKX 成交获取失败: {exc.msg}", 502) from exc
    return {"inst_id": inst_id, "count": len(rows), "trades": rows}

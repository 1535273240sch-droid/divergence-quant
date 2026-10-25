"""市场选币候选池与持仓状态路由。"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

from fastapi import APIRouter

from dquant.api.auth import CurrentUser
from dquant.contracts.models import Position, ScreenerCandidate
from dquant.contracts.types import DivergenceKind, PositionSide, RegimeState

router = APIRouter(prefix="/api/v1/market", tags=["行情与持仓"])

# 内存模拟持仓与候选（支持 M12 前端与集成测试）
_mock_candidates: list[dict[str, Any]] = [
    {
        "inst_id": "BTC-USDT-SWAP",
        "pool": "A",
        "regime": "UPTREND",
        "divergence_id": "div_001",
        "divergence_kind": "BULL_REGULAR",
        "score": 92.5,
        "rank": 1,
        "updated_ts_ms": int(time.time() * 1000),
    },
    {
        "inst_id": "ETH-USDT-SWAP",
        "pool": "A",
        "regime": "UPTREND",
        "divergence_id": "div_002",
        "divergence_kind": "BULL_REGULAR",
        "score": 88.0,
        "rank": 2,
        "updated_ts_ms": int(time.time() * 1000),
    },
]

_mock_positions: list[dict[str, Any]] = [
    {
        "inst_id": "BTC-USDT-SWAP",
        "pos_side": "long",
        "size": "0.5",
        "avg_price": "64200.0",
        "mark_price": "65100.0",
        "upl": "450.0",
        "stop_loss": "63000.0",
        "strategy": "S1 顺势回调",
        "opened_ts_ms": int(time.time() * 1000) - 3600_000,
    }
]


@router.get("/candidates", response_model=list[ScreenerCandidate], summary="获取多因子选币候选池")
async def list_candidates(user: CurrentUser) -> list[ScreenerCandidate]:
    """返回通过选币器筛选的 Top N 候选标的。"""
    return [
        ScreenerCandidate(
            inst_id=c["inst_id"],
            pool=c["pool"],
            regime=RegimeState(c["regime"]),
            divergence_id=c.get("divergence_id"),
            divergence_kind=DivergenceKind(c["divergence_kind"])
            if c.get("divergence_kind")
            else None,
            score=c["score"],
            rank=c["rank"],
            updated_ts_ms=c["updated_ts_ms"],
        )
        for c in _mock_candidates
    ]


@router.get("/positions", response_model=list[Position], summary="获取当前持仓列表")
async def list_positions(user: CurrentUser) -> list[Position]:
    """返回当前活动持仓及浮动盈亏。"""
    return [
        Position(
            inst_id=p["inst_id"],
            pos_side=PositionSide(p["pos_side"]),
            size=Decimal(str(p["size"])),
            avg_price=Decimal(str(p["avg_price"])),
            mark_price=Decimal(str(p["mark_price"])),
            upl=Decimal(str(p["upl"])),
            stop_loss=Decimal(str(p["stop_loss"])) if p.get("stop_loss") else None,
            strategy=p["strategy"],
            opened_ts_ms=p["opened_ts_ms"],
        )
        for p in _mock_positions
    ]

"""MHD 多周期绩效 API。"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from dquant.analytics.mhd_backtest import (
    DEFAULT_TIMEFRAMES,
    latest_results,
    run_mhd_backtests,
)
from dquant.api.auth import CurrentUser, OptionalUser

router = APIRouter(prefix="/api/v1/mhd", tags=["MHD 绩效"])


class BacktestRequest(BaseModel):
    timeframes: list[str] = Field(default_factory=lambda: list(DEFAULT_TIMEFRAMES))
    inst_ids: list[str] | None = None
    max_bars: int = Field(default=5000, ge=200, le=10000)


@router.get("/performance", summary="获取各周期最近一次 MHD 回测绩效")
async def get_performance(user: OptionalUser) -> dict[str, Any]:
    del user
    rows = latest_results()
    return {"count": len(rows), "results": rows}


@router.post("/backtest", summary="运行 MHD 多周期历史回测并落库")
async def run_backtest(req: BacktestRequest, user: CurrentUser) -> dict[str, Any]:
    del user
    stats = await asyncio.to_thread(
        run_mhd_backtests,
        timeframes=req.timeframes,
        inst_ids=req.inst_ids,
        max_bars=req.max_bars,
    )
    return {
        "count": len(stats),
        "results": [
            {
                "timeframe": item.timeframe,
                "metrics": item.to_dict(include_trades=False),
            }
            for item in stats
        ],
    }

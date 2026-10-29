"""MHD 多周期绩效 API。"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from dquant.analytics.mhd_backtest import (
    DEFAULT_TIMEFRAMES,
    latest_results,
    latest_trades,
    run_mhd_backtests,
)
from dquant.api.auth import CurrentUser, OptionalUser

router = APIRouter(prefix="/api/v1/mhd", tags=["MHD 绩效"])


class BacktestRequest(BaseModel):
    timeframes: list[str] = Field(default_factory=lambda: list(DEFAULT_TIMEFRAMES))
    inst_ids: list[str] | None = None
    max_bars: int = Field(default=5000, ge=200, le=10000)
    atr_mult: float = Field(default=2.5, gt=0, le=10)
    atr_period: int = Field(default=14, ge=2, le=200)
    entry_mode: str = Field(default="mhd", pattern="^(body|mhd|mhd_body)$")
    rr_ratio: float = Field(default=1.0, gt=0, le=10)
    mhd_filter: bool = True
    mhd_filter_lookback: int = Field(default=20, ge=1, le=200)
    entry_window: int = Field(default=20, ge=1, le=200)
    fee_rate: float = Field(default=0.0005, ge=0, le=0.1)
    slippage_rate: float = Field(default=0.0002, ge=0, le=0.1)
    funding_rate_8h: float = Field(default=0.0001, ge=-0.1, le=0.1)


@router.get("/performance", summary="获取各周期最近一次 MHD 回测绩效")
async def get_performance(user: OptionalUser) -> dict[str, Any]:
    del user
    rows = latest_results()
    return {"count": len(rows), "results": rows}


@router.get("/trades", summary="获取指定周期最近一次回测的开平仓点")
async def get_trades(
    user: OptionalUser,
    timeframe: str = "1H",
    mode: str = "mhd",
) -> dict[str, Any]:
    del user
    trades = latest_trades(timeframe, entry_mode=mode)
    return {"timeframe": timeframe, "mode": mode, "count": len(trades), "trades": trades}


@router.post("/backtest", summary="运行 MHD 多周期历史回测并落库")
async def run_backtest(req: BacktestRequest, user: CurrentUser) -> dict[str, Any]:
    del user
    stats = await asyncio.to_thread(
        run_mhd_backtests,
        timeframes=req.timeframes,
        inst_ids=req.inst_ids,
        max_bars=req.max_bars,
        atr_mult=Decimal(str(req.atr_mult)),
        atr_period=req.atr_period,
        entry_mode=req.entry_mode,
        rr_ratio=req.rr_ratio,
        mhd_filter=req.mhd_filter,
        mhd_filter_lookback=req.mhd_filter_lookback,
        entry_window=req.entry_window,
        fee_rate=req.fee_rate,
        slippage_rate=req.slippage_rate,
        funding_rate_8h=req.funding_rate_8h,
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

"""系统健康检查与状态监控路由。"""

from __future__ import annotations

import time

from fastapi import APIRouter

from dquant.api import live_data
from dquant.api.auth import OptionalUser
from dquant.api.schemas import HealthResponse

router = APIRouter(prefix="/api/v1", tags=["监控与健康"])
_start_time = time.time()


@router.get("/health", response_model=HealthResponse, summary="系统健康检查")
async def health_check() -> HealthResponse:
    """返回当前系统运行健康状态、启动时长与时间戳。用于 Docker 与 K8s 存活探针。"""
    now = time.time()
    return HealthResponse(
        status="ok",
        version="0.1.0",
        mode="demo",
        uptime_seconds=round(now - _start_time, 2),
        timestamp=int(now * 1000),
    )


@router.get("/overview", summary="引擎运行概览（数据新鲜度 / 池子 / 计数）")
async def engine_overview(user: OptionalUser) -> dict:
    """返回引擎库的实时概览，供前端顶栏与状态条展示真实运行状态。"""

    del user
    data = live_data.overview()
    data["api_uptime_seconds"] = round(time.time() - _start_time, 2)
    return data

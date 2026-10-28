"""FastAPI 核心应用组装入口。

整合：
1. 统一错误处理器。
2. 跨域 CORS 支持与安全响应头。
3. 路由注册：认证、待确认信号与下单、策略管理、紧急熔断、选币持仓、健康探针。
4. WebSocket 长连接实时广播。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

from dquant.api.errors import (
    APIException,
    api_exception_handler,
    generic_exception_handler,
    validation_exception_handler,
)
from dquant.api.routes.account_routes import router as account_router
from dquant.api.routes.auth_routes import router as auth_router
from dquant.api.routes.emergency_routes import router as emergency_router
from dquant.api.routes.exchange_routes import router as exchange_router
from dquant.api.routes.factor_routes import router as factor_router
from dquant.api.routes.health_routes import router as health_router
from dquant.api.routes.market_routes import router as market_router
from dquant.api.routes.signal_routes import router as signal_router
from dquant.api.routes.strategy_routes import router as strategy_router
from dquant.api.websocket import ws_manager


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """添加基础安全响应头的中间件。"""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        return response


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例。"""
    app = FastAPI(
        title="DivergenceQuant API",
        version="0.1.0",
        description="DivergenceQuant 核心量化服务接口，支持半自动确认下单、风控熔断与实时广播",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # 1. 注册中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(SecurityHeadersMiddleware)

    # 2. 注册统一异常处理器
    app.add_exception_handler(APIException, api_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)

    # 3. 注册业务路由
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(account_router)
    app.include_router(factor_router)
    app.include_router(signal_router)
    app.include_router(strategy_router)
    app.include_router(emergency_router)
    app.include_router(exchange_router)
    app.include_router(market_router)

    # 4. 注册 WebSocket 实时流
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await ws_manager.connect(websocket)
        try:
            while True:
                data = await websocket.receive_text()
                await ws_manager.handle_client_message(websocket, data)
        except WebSocketDisconnect:
            ws_manager.disconnect(websocket)
        except Exception:
            ws_manager.disconnect(websocket)

    # 5. 挂载一体化前端静态资源（web/ice-console）
    frontend_dir: Path | None = None
    candidates: list[Path] = []
    if os.getenv("DQUANT_FRONTEND_DIR"):
        candidates.append(Path(os.environ["DQUANT_FRONTEND_DIR"]))
    if hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "web" / "ice-console")
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        candidates.append(exe_dir / "web" / "ice-console")
        candidates.append(exe_dir / "_internal" / "web" / "ice-console")
    candidates.extend([
        Path.cwd() / "web" / "ice-console",
        Path(__file__).resolve().parent.parent.parent.parent / "web" / "ice-console",
        Path(__file__).resolve().parent.parent.parent / "web" / "ice-console",
    ])
    for c in candidates:
        if c and c.is_dir() and (c / "index.html").is_file():
            frontend_dir = c
            break

    if frontend_dir is not None:
        app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")

    return app


app = create_app()

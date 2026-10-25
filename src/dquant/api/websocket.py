"""WebSocket 实时通信与长连接推送管理器。

支持：
1. 客户端连接与注销管理。
2. 双向心跳 (ping / pong)。
3. 广播实时交易信号更新、紧急熔断状态和订单成交动态。
4. 连接异常安全自动清理。
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import WebSocket
from loguru import logger


class WebSocketManager:
    """WebSocket 客户端长连接广播管理器。"""

    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        """接受新的 WebSocket 连接。"""
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info("WebSocket 客户端已建立连接 (当前连接数: {})", len(self.active_connections))
        # 初始握手问候
        await websocket.send_text(
            json.dumps(
                {
                    "type": "CONNECTED",
                    "message": "DivergenceQuant Realtime Stream Ready",
                    "timestamp": int(time.time() * 1000),
                }
            )
        )

    def disconnect(self, websocket: WebSocket) -> None:
        """移除断开的客户端。"""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info("WebSocket 客户端已断开 (当前剩余连接数: {})", len(self.active_connections))

    async def broadcast(self, message: dict[str, Any]) -> None:
        """向所有活跃客户端广播消息。"""
        if not self.active_connections:
            return

        payload = json.dumps(message)
        dead_connections: list[WebSocket] = []

        for connection in list(self.active_connections):
            try:
                await connection.send_text(payload)
            except Exception as exc:
                logger.warning("WebSocket 发送失败，标记为失效: {}", exc)
                dead_connections.append(connection)

        for dead in dead_connections:
            self.disconnect(dead)

    async def handle_client_message(self, websocket: WebSocket, text: str) -> None:
        """处理客户端上行消息（例如 ping 心跳）。"""
        try:
            data = json.loads(text)
            msg_type = data.get("type", "").upper()
            if msg_type == "PING":
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "PONG",
                            "timestamp": int(time.time() * 1000),
                        }
                    )
                )
        except Exception:
            pass


ws_manager = WebSocketManager()

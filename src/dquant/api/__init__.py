"""DivergenceQuant API 边界（由 M11 实现）。

包含 FastAPI 路由、鉴权、强制 TOTP、确认下单、策略管理、紧急开关与 WebSocket。
"""

from dquant.api.app import app, create_app
from dquant.api.audit import AuditService, sanitize_dict
from dquant.api.auth import (
    brute_force_protector,
    compute_totp,
    confirmation_token_manager,
    create_access_token,
    decode_access_token,
    generate_totp_secret,
    get_current_user,
    get_totp_uri,
    verify_totp,
)
from dquant.api.config import APISettings, load_api_settings
from dquant.api.errors import (
    AccountLockedException,
    APIException,
    ForbiddenException,
    NotFoundException,
    UnauthorizedException,
)
from dquant.api.websocket import WebSocketManager, ws_manager

__all__ = [
    "app",
    "create_app",
    "AuditService",
    "sanitize_dict",
    "brute_force_protector",
    "compute_totp",
    "confirmation_token_manager",
    "create_access_token",
    "decode_access_token",
    "generate_totp_secret",
    "get_current_user",
    "get_totp_uri",
    "verify_totp",
    "APISettings",
    "load_api_settings",
    "APIException",
    "AccountLockedException",
    "ForbiddenException",
    "NotFoundException",
    "UnauthorizedException",
    "WebSocketManager",
    "ws_manager",
]

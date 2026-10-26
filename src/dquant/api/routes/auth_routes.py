"""认证与安全会话路由。

多用户模式：开放注册 + 密码登录（不再强制 TOTP），JWT 会话。
管理员账号仍可通过环境变量登录，用于兜底运维。
"""

from __future__ import annotations

import hashlib
import time

from fastapi import APIRouter, Request

from dquant.api import users as user_store
from dquant.api.audit import AuditService
from dquant.api.auth import (
    CurrentUser,
    brute_force_protector,
    create_access_token,
    generate_totp_secret,
    get_totp_uri,
    verify_totp,
)
from dquant.api.config import load_api_settings
from dquant.api.errors import AccountLockedException, APIException, UnauthorizedException
from dquant.api.schemas import (
    LoginRequest,
    RegisterRequest,
    StandardSuccessResponse,
    TokenResponse,
    TotpSetupResponse,
    TotpVerifyRequest,
    UserInfo,
)

router = APIRouter(prefix="/api/v1/auth", tags=["认证"])
_settings = load_api_settings()
_audit_service = AuditService()

# 注册防刷：每个 IP 每小时最多 5 个账号
_REGISTER_LOG: dict[str, list[float]] = {}
_REGISTER_LIMIT = 5
_REGISTER_WINDOW_SEC = 3600


def _register_allowed_for_ip(ip: str) -> bool:
    now = time.time()
    stamps = [t for t in _REGISTER_LOG.get(ip, []) if now - t < _REGISTER_WINDOW_SEC]
    _REGISTER_LOG[ip] = stamps
    if len(stamps) >= _REGISTER_LIMIT:
        return False
    stamps.append(now)
    return True


def _issue_token(username: str, role: str) -> TokenResponse:
    access_token = create_access_token(
        data={"sub": username, "role": role},
        settings=_settings,
    )
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in_sec=_settings.access_token_expire_minutes * 60,
        username=username,
    )


@router.post("/register", response_model=TokenResponse, summary="注册新用户（密码登录）")
async def register(req: RegisterRequest, request: Request) -> TokenResponse:
    """开放注册：用户名 + 密码即可创建账户，注册成功即签发 JWT。"""

    if not user_store.registration_allowed():
        raise APIException("REGISTRATION_DISABLED", "管理员已关闭自助注册", 403)
    client_ip = request.client.host if request.client else "unknown"
    if not _register_allowed_for_ip(client_ip):
        raise APIException("RATE_LIMITED", "注册过于频繁，请稍后再试", 429)
    try:
        record = user_store.create_user(req.username, req.password)
    except user_store.UserStoreError as exc:
        raise APIException("INVALID_REGISTRATION", str(exc), 400) from exc

    await _audit_service.log_action(
        user=record["username"],
        action="REGISTER_SUCCESS",
        after={"client_ip": client_ip},
    )
    return _issue_token(record["username"], record["role"])


@router.post("/login", response_model=TokenResponse, summary="用户登录（密码）")
async def login(req: LoginRequest, request: Request) -> TokenResponse:
    """用户名 + 密码登录；连续失败 5 次锁定 15 分钟。"""

    client_ip = request.client.host if request.client else "unknown"
    identifier = f"{req.username}@{client_ip}"

    if brute_force_protector.is_locked(identifier):
        raise AccountLockedException(
            message=f"账号 [{req.username}] 尝试失败次数超限，系统已安全锁定 15 分钟"
        )

    record = user_store.authenticate(req.username, req.password)

    # 兼容旧部署：环境变量里的管理员账号仍可登录
    if record is None:
        input_hash = hashlib.sha256(req.password.encode()).hexdigest()
        if (
            req.username == _settings.admin_username
            and input_hash == _settings.admin_password_hash
        ):
            record = {
                "username": _settings.admin_username,
                "role": "admin",
                "id": 0,
            }

    if record is None:
        remains = brute_force_protector.record_failure(identifier)
        await _audit_service.log_action(
            user=req.username,
            action="LOGIN_FAILED_PASSWORD",
            before={"client_ip": client_ip, "remains": remains},
        )
        if remains == 0:
            raise AccountLockedException()
        raise UnauthorizedException(message=f"用户名或密码错误，剩余尝试次数: {remains}")

    brute_force_protector.record_success(identifier)
    await _audit_service.log_action(
        user=req.username,
        action="LOGIN_SUCCESS",
        after={"client_ip": client_ip},
    )
    return _issue_token(record["username"], str(record.get("role") or "user"))


@router.get("/totp/setup", response_model=TotpSetupResponse, summary="生成 TOTP 绑定秘钥与 URI")
async def setup_totp(user: CurrentUser) -> TotpSetupResponse:
    """可选功能：生成 TOTP 秘钥与 URI；当前登录流程不再强制校验。"""

    secret = generate_totp_secret()
    uri = get_totp_uri(secret, username=user["username"])
    return TotpSetupResponse(secret=secret, otpauth_url=uri)


@router.post(
    "/totp/verify", response_model=StandardSuccessResponse, summary="验证 TOTP 秘钥与输入码"
)
async def verify_user_totp(req: TotpVerifyRequest, user: CurrentUser) -> StandardSuccessResponse:
    """验证用户输入的 TOTP 动态码是否与秘钥匹配（可选）。"""

    if not verify_totp(req.secret, req.totp_code, window=1):
        raise APIException(code="INVALID_TOTP", message="输入的 TOTP 动态验证码校验不匹配")
    return StandardSuccessResponse(message="TOTP 验证通过")


@router.get("/me", response_model=UserInfo, summary="获取当前登录用户信息")
async def get_me(user: CurrentUser) -> UserInfo:
    """获取当前已登录用户的用户名与角色权限。"""

    record = user_store.get_user(user["username"])
    role = str(record["role"]) if record else user.get("role", "user")
    return UserInfo(username=user["username"], role=role)

"""认证与安全会话路由。

实现用户名密码 + 强制 TOTP 校验、防爆破保护与 JWT 会话管理。
"""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Request

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
    StandardSuccessResponse,
    TokenResponse,
    TotpSetupResponse,
    TotpVerifyRequest,
    UserInfo,
)

router = APIRouter(prefix="/api/v1/auth", tags=["认证"])
_settings = load_api_settings()
_audit_service = AuditService()


@router.post("/login", response_model=TokenResponse, summary="用户登录（密码 + 强制 TOTP）")
async def login(req: LoginRequest, request: Request) -> TokenResponse:
    """登录接口。

    必须同时提供用户名、密码和 6 位 TOTP 动态验证码。
    连续密码错误达到 5 次将触发 15 分钟安全锁定。
    """
    client_ip = request.client.host if request.client else "unknown"
    identifier = f"{req.username}@{client_ip}"

    # 1. 检查是否已被安全锁定
    if brute_force_protector.is_locked(identifier):
        raise AccountLockedException(
            message=f"账号 [{req.username}] 尝试失败次数超限，系统已安全锁定 15 分钟"
        )

    # 2. 校验用户名与密码哈希
    input_hash = hashlib.sha256(req.password.encode()).hexdigest()
    is_user_valid = (
        req.username == _settings.admin_username and input_hash == _settings.admin_password_hash
    )

    if not is_user_valid:
        remains = brute_force_protector.record_failure(identifier)
        await _audit_service.log_action(
            user=req.username,
            action="LOGIN_FAILED_PASSWORD",
            before={"client_ip": client_ip, "remains": remains},
        )
        if remains == 0:
            raise AccountLockedException()
        raise UnauthorizedException(message=f"用户名或密码错误，剩余尝试次数: {remains}")

    # 3. 校验强制 TOTP 动态码
    totp_valid = verify_totp(_settings.admin_totp_secret, req.totp_code, window=1)
    if not totp_valid:
        remains = brute_force_protector.record_failure(identifier)
        await _audit_service.log_action(
            user=req.username,
            action="LOGIN_FAILED_TOTP",
            before={"client_ip": client_ip, "remains": remains},
        )
        if remains == 0:
            raise AccountLockedException()
        raise UnauthorizedException(message=f"TOTP 动态二次验证码错误，剩余尝试次数: {remains}")

    # 4. 登录成功，重置失败计数并签发 JWT
    brute_force_protector.record_success(identifier)
    access_token = create_access_token(
        data={"sub": req.username, "role": "admin"},
        settings=_settings,
    )

    await _audit_service.log_action(
        user=req.username,
        action="LOGIN_SUCCESS",
        after={"client_ip": client_ip},
    )

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in_sec=_settings.access_token_expire_minutes * 60,
        username=req.username,
    )


@router.get("/totp/setup", response_model=TotpSetupResponse, summary="生成 TOTP 绑定秘钥与 URI")
async def setup_totp(user: CurrentUser) -> TotpSetupResponse:
    """生成用于绑定身份验证器 (Google Authenticator/1Password) 的新 TOTP 秘钥与 URI。"""
    secret = generate_totp_secret()
    uri = get_totp_uri(secret, username=user["username"])
    return TotpSetupResponse(secret=secret, otpauth_url=uri)


@router.post(
    "/totp/verify", response_model=StandardSuccessResponse, summary="验证 TOTP 秘钥与输入码"
)
async def verify_user_totp(req: TotpVerifyRequest, user: CurrentUser) -> StandardSuccessResponse:
    """验证用户输入的 TOTP 动态码是否与秘钥匹配。"""
    if not verify_totp(req.secret, req.totp_code, window=1):
        raise APIException(code="INVALID_TOTP", message="输入的 TOTP 动态验证码校验不匹配")
    return StandardSuccessResponse(message="TOTP 验证通过")


@router.get("/me", response_model=UserInfo, summary="获取当前登录用户信息")
async def get_me(user: CurrentUser) -> UserInfo:
    """获取当前已登录用户的用户名与角色权限。"""
    return UserInfo(username=user["username"], role=user["role"])

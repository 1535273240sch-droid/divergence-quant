"""认证、强制 TOTP、JWT 会话与一次性确认下单令牌管理。

严格遵循计划书与安全规范：
1. RFC 6238 标准 TOTP（HMAC-SHA1 动态截断，支持时钟窗口容忍）。
2. 用户名密码 + 强制 TOTP 双因素登录。
3. 登录失败 5 次锁定 15 分钟防暴力破解。
4. 一次性确认链接令牌（HMAC-SHA256 签名，15 分钟过期，防重放与 CSRF）。
5. 敏感接口 JWT 校验拦截。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import Annotated, Any

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger

from dquant.api.config import APISettings, load_api_settings
from dquant.api.errors import UnauthorizedException

# ---------------------------------------------------------------------------
# RFC 6238 TOTP 标准实现
# ---------------------------------------------------------------------------


def generate_totp_secret(length: int = 20) -> str:
    """生成 Base32 编码的 TOTP 密钥。"""
    raw_bytes = secrets.token_bytes(length)
    return base64.b32encode(raw_bytes).decode("ascii").rstrip("=")


def get_totp_uri(secret: str, username: str, issuer: str = "DivergenceQuant") -> str:
    """生成符合 Google Authenticator / 1Password 格式的 otpauth URI。"""
    # 补齐 base32 padding
    pad_len = (8 - len(secret) % 8) % 8
    padded_secret = secret + ("=" * pad_len)
    return f"otpauth://totp/{issuer}:{username}?secret={padded_secret}&issuer={issuer}&algorithm=SHA1&digits=6&period=30"


def compute_totp(secret: str, t: float | None = None, interval: int = 30) -> str:
    """基于 RFC 6238 标准计算指定时间戳的 6 位 TOTP 动态码。"""
    if t is None:
        t = time.time()
    counter = int(t // interval)

    # 规范化 Base32 padding
    secret_upper = secret.strip().upper()
    pad_len = (8 - len(secret_upper) % 8) % 8
    padded_secret = secret_upper + ("=" * pad_len)
    key = base64.b32decode(padded_secret, casefold=True)

    # 转换为 8 字节大端整数
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()

    # 动态截断 (Dynamic Truncation)
    offset = digest[-1] & 0x0F
    code_int = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    code = code_int % 1_000_000
    return f"{code:06d}"


def verify_totp(
    secret: str, code: str, window: int = 1, interval: int = 30, now: float | None = None
) -> bool:
    """验证输入的 TOTP 动态码，支持前后 window 个时间步长防时钟偏移。"""
    if now is None:
        now = time.time()
    code_str = code.strip()
    if len(code_str) != 6 or not code_str.isdigit():
        return False

    for step in range(-window, window + 1):
        t = now + step * interval
        expected = compute_totp(secret, t=t, interval=interval)
        if hmac.compare_digest(expected, code_str):
            return True
    return False


# ---------------------------------------------------------------------------
# 登录防暴力破解锁定
# ---------------------------------------------------------------------------


class BruteForceProtector:
    """防暴力破解锁定保护器。"""

    def __init__(self, max_failures: int = 5, lockout_seconds: int = 900) -> None:
        self.max_failures = max_failures
        self.lockout_seconds = lockout_seconds
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def is_locked(self, identifier: str, now: float | None = None) -> bool:
        """检查特定用户或 IP 是否正处于安全锁定中。"""
        if now is None:
            now = time.time()
        locked_time = self._locked_until.get(identifier, 0.0)
        return now < locked_time

    def record_failure(self, identifier: str, now: float | None = None) -> int:
        """记录一次失败登录。达到上限将触发锁定。返回剩余尝试次数。"""
        if now is None:
            now = time.time()
        attempts = self._failures.setdefault(identifier, [])
        # 清除超过 lockout_seconds 前的历史
        cutoff = now - self.lockout_seconds
        attempts = [t for t in attempts if t > cutoff]
        attempts.append(now)
        self._failures[identifier] = attempts

        if len(attempts) >= self.max_failures:
            self._locked_until[identifier] = now + self.lockout_seconds
            logger.warning(
                "账号安全告警: 连续错误 {} 次，已锁定 identifier={} 持续 {} 秒",
                len(attempts),
                identifier,
                self.lockout_seconds,
            )
            return 0
        return max(0, self.max_failures - len(attempts))

    def record_success(self, identifier: str) -> None:
        """登录成功，清除该实体的错误与锁定记录。"""
        self._failures.pop(identifier, None)
        self._locked_until.pop(identifier, None)

    def reset(self) -> None:
        """重置状态（测试使用）。"""
        self._failures.clear()
        self._locked_until.clear()


# ---------------------------------------------------------------------------
# 一次性确认链接令牌引擎
# ---------------------------------------------------------------------------


class ConfirmationTokenManager:
    """一次性确认链接令牌管理器。

    - 15 分钟 TTL
    - HMAC 签名校验防篡改
    - 一次性标记防重放
    """

    def __init__(self, secret_key: str, ttl_sec: int = 900) -> None:
        self.secret_key = secret_key.encode("utf-8")
        self.ttl_sec = ttl_sec
        # 记录已消费的 token 防止重放
        self._consumed_tokens: set[str] = set()

    def create_token(self, signal_id: str, now: float | None = None) -> str:
        """生成带有时效与签名的二次确认令牌。"""
        if now is None:
            now = time.time()
        expires_at = int(now + self.ttl_sec)
        nonce = secrets.token_hex(8)
        message = f"{signal_id}:{expires_at}:{nonce}"
        sig = hmac.new(self.secret_key, message.encode("utf-8"), hashlib.sha256).hexdigest()
        token = f"{message}:{sig}"
        return base64.urlsafe_b64encode(token.encode("utf-8")).decode("ascii")

    def verify_and_consume(
        self, token: str, expected_signal_id: str, now: float | None = None
    ) -> bool:
        """验证令牌有效性并立即消费（仅能使用一次）。"""
        if now is None:
            now = time.time()
        if not token:
            return False

        try:
            raw_text = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
            parts = raw_text.split(":")
            if len(parts) != 4:
                return False
            sig_id, exp_str, nonce, signature = parts

            # 校验 signal_id
            if sig_id != expected_signal_id:
                return False

            # 校验是否已使用
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            if token_hash in self._consumed_tokens:
                logger.warning("确认令牌重放攻击被拦截: token_hash={}", token_hash)
                return False

            # 校验签名
            message = f"{sig_id}:{exp_str}:{nonce}"
            expected_sig = hmac.new(
                self.secret_key, message.encode("utf-8"), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(signature, expected_sig):
                return False

            # 校验是否过期
            expires_at = int(exp_str)
            if now > expires_at:
                logger.info("确认令牌已过期: now={}, expires_at={}", now, expires_at)
                return False

            # 标记为已消费
            self._consumed_tokens.add(token_hash)
            return True
        except Exception as exc:
            logger.warning("确认令牌验证异常: {}", exc)
            return False


# ---------------------------------------------------------------------------
# 全局实例与 FastAPI 依赖项
# ---------------------------------------------------------------------------

_api_settings = load_api_settings()
brute_force_protector = BruteForceProtector(
    max_failures=_api_settings.max_login_failures,
    lockout_seconds=_api_settings.lockout_duration_sec,
)
confirmation_token_manager = ConfirmationTokenManager(
    secret_key=_api_settings.jwt_secret,
    ttl_sec=_api_settings.confirmation_token_ttl_sec,
)
http_bearer = HTTPBearer(auto_error=False)


def create_access_token(data: dict[str, Any], settings: APISettings | None = None) -> str:
    """生成 JWT 访问令牌。"""
    cfg = settings or _api_settings
    to_encode = data.copy()
    expire_sec = cfg.access_token_expire_minutes * 60
    now = time.time()
    to_encode.update({"iat": int(now), "exp": int(now + expire_sec)})
    return jwt.encode(to_encode, cfg.jwt_secret, algorithm=cfg.jwt_algorithm)


def decode_access_token(token: str, settings: APISettings | None = None) -> dict[str, Any]:
    """解析并校验 JWT 访问令牌。"""
    cfg = settings or _api_settings
    try:
        return jwt.decode(token, cfg.jwt_secret, algorithms=[cfg.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        raise UnauthorizedException(message="访问令牌已过期，请重新登录") from None
    except Exception as exc:
        raise UnauthorizedException(message="无效的认证令牌", detail=str(exc)) from exc


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(http_bearer)] = None,
) -> dict[str, Any]:
    """FastAPI 依赖项：验证 Bearer Token 并获取当前登录用户信息。"""
    if credentials is None or not credentials.credentials:
        raise UnauthorizedException(message="需要登录认证后才能访问此接口")
    token = credentials.credentials
    payload = decode_access_token(token)
    username = payload.get("sub")
    if not username:
        raise UnauthorizedException(message="认证令牌 payload 缺失用户名")
    return {"username": username, "role": payload.get("role", "admin")}


CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]

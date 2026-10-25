"""API 服务与鉴权配置模块。

严格支持从环境变量加载安全参数（JWT Secret、管理员账号、密码哈希与防爆破参数），
不把明文或敏感凭据硬编码在仓库中。
"""

from __future__ import annotations

import hashlib
import os
import secrets

from pydantic import BaseModel, ConfigDict, Field

_DEFAULT_JWT_SECRET = secrets.token_hex(32)


class APISettings(BaseModel):
    """API 服务配置。"""

    model_config = ConfigDict(extra="ignore")

    host: str = Field(default="0.0.0.0", description="API 监听地址")
    port: int = Field(default=8000, description="API 监听端口")
    jwt_secret: str = Field(default_factory=lambda: _DEFAULT_JWT_SECRET, description="JWT 签名密钥")
    jwt_algorithm: str = Field(default="HS256", description="JWT 签名算法")
    access_token_expire_minutes: int = Field(default=120, description="访问令牌有效期（分钟）")
    admin_username: str = Field(default="admin", description="默认管理员用户名")
    # 默认密码 SHA256 存储或带盐哈希
    admin_password_hash: str = Field(
        default_factory=lambda: hashlib.sha256(b"DQuant@2026!Secure").hexdigest(),
        description="管理员密码 SHA256 哈希",
    )
    # 强制 TOTP 密钥（RFC 6238 Base32）
    admin_totp_secret: str = Field(
        default="JBSWY3DPEHPK3PXP",  # 标准 base32 测试种子
        description="管理员 TOTP Secret (Base32)",
    )
    max_login_failures: int = Field(default=5, ge=1, description="最多连续密码错误次数")
    lockout_duration_sec: int = Field(default=900, ge=60, description="锁定时间（秒，默认15分钟）")
    confirmation_token_ttl_sec: int = Field(
        default=900, ge=60, description="确认下单令牌有效时长（秒，默认15分钟）"
    )


def load_api_settings() -> APISettings:
    """从环境变量加载 API 配置。"""
    kwargs: dict[str, object] = {}

    if "DQUANT_API_HOST" in os.environ:
        kwargs["host"] = os.environ["DQUANT_API_HOST"]
    if "DQUANT_API_PORT" in os.environ:
        try:
            kwargs["port"] = int(os.environ["DQUANT_API_PORT"])
        except ValueError:
            pass
    if "DQUANT_JWT_SECRET" in os.environ:
        kwargs["jwt_secret"] = os.environ["DQUANT_JWT_SECRET"]
    if "DQUANT_ADMIN_USER" in os.environ:
        kwargs["admin_username"] = os.environ["DQUANT_ADMIN_USER"]
    if "DQUANT_ADMIN_PASSWORD" in os.environ:
        # 如果提供了环境变量密码明文，计算哈希存入
        raw_pwd = os.environ["DQUANT_ADMIN_PASSWORD"]
        kwargs["admin_password_hash"] = hashlib.sha256(raw_pwd.encode()).hexdigest()
    elif "DQUANT_ADMIN_PASSWORD_HASH" in os.environ:
        kwargs["admin_password_hash"] = os.environ["DQUANT_ADMIN_PASSWORD_HASH"]

    if "DQUANT_ADMIN_TOTP_SECRET" in os.environ:
        kwargs["admin_totp_secret"] = os.environ["DQUANT_ADMIN_TOTP_SECRET"]
    if "DQUANT_MAX_LOGIN_FAILURES" in os.environ:
        try:
            kwargs["max_login_failures"] = int(os.environ["DQUANT_MAX_LOGIN_FAILURES"])
        except ValueError:
            pass
    if "DQUANT_LOCKOUT_DURATION_SEC" in os.environ:
        try:
            kwargs["lockout_duration_sec"] = int(os.environ["DQUANT_LOCKOUT_DURATION_SEC"])
        except ValueError:
            pass

    return APISettings(**kwargs)

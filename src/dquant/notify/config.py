"""通知系统配置模块。

支持从环境变量与默认值加载邮件 SMTP 与告警限流参数。
密码与敏感信息仅通过环境变量传递，不硬编码在代码或仓库文件中。
"""

from __future__ import annotations

import os

from pydantic import BaseModel, ConfigDict, Field


class EmailSettings(BaseModel):
    """邮件发送与 SMTP 配置。"""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = Field(default=True, description="是否启用邮件通知")
    host: str = Field(default="smtp.example.com", description="SMTP 主机地址")
    port: int = Field(default=587, description="SMTP 端口")
    user: str = Field(default="", description="SMTP 用户名/账号")
    password: str = Field(default="", description="SMTP 密码/授权码")
    use_tls: bool = Field(default=True, description="是否启用 STARTTLS")
    use_ssl: bool = Field(default=False, description="是否启用 SSL 直连")
    from_addr: str = Field(default="noreply@dquant.local", description="发件人地址")
    to_addrs: list[str] = Field(
        default_factory=lambda: ["trader@dquant.local"], description="收件人列表"
    )
    hourly_rate_limit: int = Field(default=30, ge=1, description="每小时最大发送邮件数量")
    timeout_sec: float = Field(default=10.0, gt=0, description="SMTP 发送超时秒数")


def load_email_settings() -> EmailSettings:
    """从环境变量加载邮件通知配置。

    支持的环境变量包括：
    - DQUANT_SMTP_ENABLED
    - DQUANT_SMTP_HOST
    - DQUANT_SMTP_PORT
    - DQUANT_SMTP_USER
    - DQUANT_SMTP_PASSWORD
    - DQUANT_SMTP_USE_TLS
    - DQUANT_SMTP_USE_SSL
    - DQUANT_SMTP_FROM
    - DQUANT_NOTIFY_TO (逗号分隔)
    - DQUANT_NOTIFY_HOURLY_LIMIT
    """
    to_raw = os.getenv("DQUANT_NOTIFY_TO", "")
    to_list = [addr.strip() for addr in to_raw.split(",") if addr.strip()] if to_raw else None

    kwargs: dict[str, object] = {}
    if "DQUANT_SMTP_ENABLED" in os.environ:
        kwargs["enabled"] = os.getenv("DQUANT_SMTP_ENABLED", "").lower() in ("1", "true", "yes")
    if "DQUANT_SMTP_HOST" in os.environ:
        kwargs["host"] = os.environ["DQUANT_SMTP_HOST"]
    if "DQUANT_SMTP_PORT" in os.environ:
        try:
            kwargs["port"] = int(os.environ["DQUANT_SMTP_PORT"])
        except ValueError:
            pass
    if "DQUANT_SMTP_USER" in os.environ:
        kwargs["user"] = os.environ["DQUANT_SMTP_USER"]
    if "DQUANT_SMTP_PASSWORD" in os.environ:
        kwargs["password"] = os.environ["DQUANT_SMTP_PASSWORD"]
    if "DQUANT_SMTP_USE_TLS" in os.environ:
        kwargs["use_tls"] = os.environ["DQUANT_SMTP_USE_TLS"].lower() in ("1", "true", "yes")
    if "DQUANT_SMTP_USE_SSL" in os.environ:
        kwargs["use_ssl"] = os.environ["DQUANT_SMTP_USE_SSL"].lower() in ("1", "true", "yes")
    if "DQUANT_SMTP_FROM" in os.environ:
        kwargs["from_addr"] = os.environ["DQUANT_SMTP_FROM"]
    if to_list:
        kwargs["to_addrs"] = to_list
    if "DQUANT_NOTIFY_HOURLY_LIMIT" in os.environ:
        try:
            kwargs["hourly_rate_limit"] = int(os.environ["DQUANT_NOTIFY_HOURLY_LIMIT"])
        except ValueError:
            pass

    return EmailSettings(**kwargs)

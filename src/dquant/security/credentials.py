"""运行时交易所凭证的加密存储。

凭证只在内存中以 ``OkxCredentials`` 使用；磁盘文件保存的是 Fernet 密文。
解密主密钥来自 ``DQUANT_JWT_SECRET``，生产环境必须显式设置且由 API 与
engine 共享，不能依赖每个进程随机生成的默认值。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from dquant.adapters.okx import OkxCredentials


class CredentialStoreError(RuntimeError):
    """凭证存储或解密失败。"""


class CredentialStore:
    """使用共享主密钥保存 OKX 凭证的本地加密文件。"""

    def __init__(self, path: str | Path | None = None, master_secret: str | None = None) -> None:
        if master_secret is not None:
            secret = master_secret.strip()
        else:
            secret = os.getenv("DQUANT_JWT_SECRET", "").strip()
            if not secret:
                jwt_file = Path("data/.jwt_secret")
                if jwt_file.is_file():
                    try:
                        secret = jwt_file.read_text(encoding="utf-8").strip()
                    except OSError:
                        pass
        if not secret:
            raise CredentialStoreError("DQUANT_JWT_SECRET 未配置，不能启用加密凭证存储")
        raw_key = hashlib.sha256(secret.encode("utf-8")).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(raw_key))
        self.path = Path(path or os.getenv("DQUANT_CREDENTIALS_FILE", "data/okx-credentials.enc"))

    def load(self) -> OkxCredentials | None:
        """读取凭证；文件不存在时返回 ``None``。"""

        if not self.path.is_file():
            return None
        try:
            payload = json.loads(self._fernet.decrypt(self.path.read_bytes()).decode("utf-8"))
            values = (payload["api_key"], payload["secret"], payload["passphrase"])
            if not all(isinstance(value, str) and value.strip() for value in values):
                raise CredentialStoreError("加密凭证文件字段不完整")
            return OkxCredentials(api_key=values[0], secret=values[1], passphrase=values[2])
        except (
            InvalidToken,
            OSError,
            ValueError,
            KeyError,
            TypeError,
            json.JSONDecodeError,
        ) as exc:
            raise CredentialStoreError("无法解密或解析 OKX 凭证文件") from exc

    def save(self, credentials: OkxCredentials) -> None:
        """原子写入凭证密文，并尽可能限制文件权限。"""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        encrypted = self._fernet.encrypt(
            json.dumps(
                {
                    "api_key": credentials.api_key,
                    "secret": credentials.secret,
                    "passphrase": credentials.passphrase,
                },
                separators=(",", ":"),
            ).encode("utf-8")
        )
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(encrypted)
            os.replace(temp_name, self.path)
        except OSError as exc:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise CredentialStoreError("无法写入加密 OKX 凭证文件") from exc

    def clear(self) -> None:
        """删除当前保存的凭证。"""

        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            raise CredentialStoreError("无法删除加密 OKX 凭证文件") from exc

"""运行时 OKX 加密凭证存储测试。"""

from pathlib import Path

import pytest

from dquant.adapters.okx import OkxCredentials
from dquant.security import CredentialStore, CredentialStoreError


def test_credential_store_round_trips_without_plaintext(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "okx.enc"
    credentials = OkxCredentials(api_key="api-key", secret="secret-value", passphrase="passphrase")
    store = CredentialStore(path, master_secret="shared-master-secret")

    store.save(credentials)

    assert store.load() == credentials
    encrypted = path.read_bytes()
    assert b"api-key" not in encrypted
    assert b"secret-value" not in encrypted

    store.clear()
    assert store.load() is None


def test_credential_store_requires_a_shared_master_secret(tmp_path: Path) -> None:
    with pytest.raises(CredentialStoreError, match="DQUANT_JWT_SECRET"):
        CredentialStore(tmp_path / "okx.enc", master_secret="")

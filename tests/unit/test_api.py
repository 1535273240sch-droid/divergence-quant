"""API 模块单元测试。

验证 TOTP 计算校验、防暴力破解锁定、一次性确认令牌生成与防重放、敏感信息脱敏和错误封装。
"""

from dquant.api.audit import sanitize_dict
from dquant.api.auth import (
    BruteForceProtector,
    ConfirmationTokenManager,
    compute_totp,
    generate_totp_secret,
    get_totp_uri,
    verify_totp,
)
from dquant.api.errors import make_error_payload


def test_totp_generation_and_verification() -> None:
    """测试 RFC 6238 TOTP 密钥生成、计算与容忍窗口验证。"""
    secret = generate_totp_secret()
    assert len(secret) >= 16

    uri = get_totp_uri(secret, username="trader_test")
    assert "otpauth://totp/" in uri
    assert "trader_test" in uri
    assert secret in uri

    now = 1700000000.0
    code = compute_totp(secret, t=now)
    assert len(code) == 6
    assert code.isdigit()

    # 验证当期 code 有效
    assert verify_totp(secret, code, now=now, window=0)

    # 验证前后 30 秒在 window=1 容忍范围内有效
    assert verify_totp(secret, code, now=now + 28, window=1)
    assert verify_totp(secret, code, now=now - 28, window=1)

    # 错误 code 拒绝
    assert not verify_totp(secret, "000000" if code != "000000" else "111111", now=now)


def test_brute_force_protector() -> None:
    """测试防暴力破解锁定：连续 5 次失败锁定 15 分钟。"""
    protector = BruteForceProtector(max_failures=5, lockout_seconds=900)
    user_id = "trader@127.0.0.1"
    t0 = 10000.0

    assert not protector.is_locked(user_id, now=t0)

    # 模拟前 4 次失败
    for i in range(1, 5):
        remains = protector.record_failure(user_id, now=t0 + i)
        assert remains == 5 - i
        assert not protector.is_locked(user_id, now=t0 + i)

    # 第 5 次失败触发锁定
    remains_final = protector.record_failure(user_id, now=t0 + 5)
    assert remains_final == 0
    assert protector.is_locked(user_id, now=t0 + 5)
    assert protector.is_locked(user_id, now=t0 + 800)

    # 超过 900 秒后自动解锁
    assert not protector.is_locked(user_id, now=t0 + 905)

    # 成功登录后清除记录
    protector.record_failure(user_id, now=t0 + 1000)
    protector.record_success(user_id)
    assert not protector.is_locked(user_id, now=t0 + 1000)


def test_confirmation_token_manager_one_time_use() -> None:
    """测试确认下单令牌：HMAC 签名、时效校验与防重放单次消费。"""
    manager = ConfirmationTokenManager(secret_key="test_secret_key_123", ttl_sec=900)
    sig_id = "sig_btc_001"
    t0 = 20000.0

    token = manager.create_token(sig_id, now=t0)
    assert token

    # 第一次使用：校验通过并被消费
    assert manager.verify_and_consume(token, expected_signal_id=sig_id, now=t0 + 60)

    # 第二次使用相同 token：应被拦截（防重放）
    assert not manager.verify_and_consume(token, expected_signal_id=sig_id, now=t0 + 65)

    # 伪造不同 signal_id 拦截
    token2 = manager.create_token(sig_id, now=t0)
    assert not manager.verify_and_consume(token2, expected_signal_id="sig_fake_999", now=t0 + 10)

    # 过期 token 拦截
    token_expired = manager.create_token(sig_id, now=t0)
    assert not manager.verify_and_consume(token_expired, expected_signal_id=sig_id, now=t0 + 901)


def test_confirmation_token_supports_strategy_ids_with_colons() -> None:
    """策略确定性 signal_id 常含冒号，令牌解析不能截断它。"""
    manager = ConfirmationTokenManager(secret_key="test_secret_key_123", ttl_sec=900)
    signal_id = "S1:1.0.0:BTC-USDT-SWAP:long:1750000000000"
    token = manager.create_token(signal_id, now=20000.0)

    assert manager.verify_and_consume(token, expected_signal_id=signal_id, now=20001.0)


def test_sensitive_data_sanitization() -> None:
    """测试敏感字段脱敏：密码/令牌替换为 ******，API Key 仅保留后 4 位。"""
    data = {
        "user": "alice",
        "password": "SuperSecretPassword123",
        "api_key": "OKX_KEY_ABC123456789",
        "nested": {
            "token": "eyJhbGciOi...",
            "totp_secret": "JBSWY3DPEHPK3PXP",
            "normal_field": 12345,
        },
        "list_items": [
            {"access_token": "secret_token_val", "name": "item1"},
        ],
    }

    sanitized = sanitize_dict(data)
    assert sanitized is not None
    assert sanitized["user"] == "alice"
    assert sanitized["password"] == "******"
    assert sanitized["api_key"] == "***...6789"
    assert sanitized["nested"]["token"] == "******"
    assert sanitized["nested"]["totp_secret"] == "******"
    assert sanitized["nested"]["normal_field"] == 12345
    assert sanitized["list_items"][0]["access_token"] == "******"
    assert sanitized["list_items"][0]["name"] == "item1"


def test_make_error_payload() -> None:
    """测试统一错误响应数据格式。"""
    payload = make_error_payload(code="TEST_ERR", message="测试错误", detail={"field": "invalid"})
    assert payload["code"] == "TEST_ERR"
    assert payload["message"] == "测试错误"
    assert payload["detail"] == {"field": "invalid"}
    assert isinstance(payload["timestamp"], int)
    assert payload["timestamp"] > 0

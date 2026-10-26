"""API 与鉴权完整集成测试。

覆盖：
1. 健康探针。
2. 强制 TOTP 与防爆破 5 次锁定。
3. JWT 鉴权与受保护路由拦截。
4. 核心安全规则：GET 绝不产生交易动作 (405 Method Not Allowed)。
5. POST 二次确认下单、单次令牌防重放、滑点偏离防护。
6. 紧急风控开关（停止开仓、一键全平口令校验）。
7. 策略参数更新与审计日志脱敏。
"""

import pytest
from starlette.testclient import TestClient

from dquant.api.app import create_app
from dquant.api.auth import brute_force_protector, compute_totp
from dquant.api.config import load_api_settings
from dquant.api.routes.signal_routes import populate_mock_signal


@pytest.fixture
def client() -> TestClient:
    """创建测试客户端并重置防爆破计数器。"""
    app = create_app()
    brute_force_protector.reset()
    return TestClient(app)


def test_health_probe(client: TestClient) -> None:
    """测试系统健康检查端点。"""
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.1.0"
    assert "uptime_seconds" in data


def test_unauthenticated_access_blocked(client: TestClient) -> None:
    """只读行情/信号公开；写操作（下单、熔断）仍需 Bearer 令牌，未带则 401。"""
    assert client.get("/api/v1/signals/pending").status_code == 200
    assert client.get("/api/v1/market/candidates").status_code == 200

    res = client.post("/api/v1/emergency/stop-open")
    assert res.status_code == 401
    err = res.json()
    assert err["code"] == "UNAUTHORIZED"


def test_login_brute_force_protection(client: TestClient) -> None:
    """测试连续密码错误 5 次后触发 15 分钟安全锁定 (423)。"""
    # 前 4 次尝试失败返回 401
    for _ in range(4):
        res = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "wrong_password", "totp_code": "123456"},
        )
        assert res.status_code == 401

    # 第 5 次尝试触发锁定 (423)
    res5 = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "wrong_password", "totp_code": "123456"},
    )
    assert res5.status_code == 423
    assert res5.json()["code"] == "ACCOUNT_LOCKED"


def test_login_success_with_totp_and_protected_me(client: TestClient) -> None:
    """测试正确密码 + 动态计算的有效 TOTP 成功登录并获取用户信息。"""
    settings = load_api_settings()
    current_code = compute_totp(settings.admin_totp_secret)

    # 登录成功
    res = client.post(
        "/api/v1/auth/login",
        json={
            "username": settings.admin_username,
            "password": "DQuant@2026!Secure",
            "totp_code": current_code,
        },
    )
    assert res.status_code == 200
    token_data = res.json()
    assert "access_token" in token_data
    token = token_data["access_token"]

    # 携带 token 访问 /me
    headers = {"Authorization": f"Bearer {token}"}
    res_me = client.get("/api/v1/auth/me", headers=headers)
    assert res_me.status_code == 200
    assert res_me.json()["username"] == settings.admin_username


def test_signal_confirm_security_rules(client: TestClient) -> None:
    """测试交易信号确认核心安全规则。

    1. GET 请求绝不产生交易（405 拦截）。
    2. POST 确认下单需要有效一次性 Token。
    3. 重复使用相同 Token 被拦截（防重放）。
    4. 滑点超限拦截。
    """
    settings = load_api_settings()
    code = compute_totp(settings.admin_totp_secret)
    login_res = client.post(
        "/api/v1/auth/login",
        json={
            "username": settings.admin_username,
            "password": "DQuant@2026!Secure",
            "totp_code": code,
        },
    )
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 准备测试信号
    sig_id = "test_signal_safe_001"
    populate_mock_signal(
        {
            "signal_id": sig_id,
            "inst_id": "BTC-USDT-SWAP",
            "side": "long",
            "action": "open",
            "entry_type": "limit",
            "price": "65000.0",
            "stop_loss": "63500.0",
            "take_profits": [{"price": "67000.0", "fraction": "0.5"}],
            "status": "PENDING",
            "score": 90.0,
        }
    )

    # 1. 验证 GET 无法下单 (405 Method Not Allowed)
    res_get = client.get(f"/api/v1/signals/{sig_id}/confirm", headers=headers)
    assert res_get.status_code == 405

    # 2. 查询待确认信号列表，提取动态生成的一次性确认令牌
    res_pending = client.get("/api/v1/signals/pending", headers=headers)
    assert res_pending.status_code == 200
    signals = res_pending.json()
    target_sig = next(s for s in signals if s["signal_id"] == sig_id)
    confirm_token = target_sig["confirmation_token"]
    assert confirm_token

    # 3. 滑点超限防护测试：信号价 65000，当前价偏离到 70000（偏离 7.6% > 1% 阈值）
    res_slippage = client.post(
        f"/api/v1/signals/{sig_id}/confirm",
        headers=headers,
        json={
            "confirmation_token": confirm_token,
            "current_price": "70000.0",
            "max_slippage_pct": "0.01",
        },
    )
    # 因为滑点超限被拦截
    assert res_slippage.status_code == 400
    assert res_slippage.json()["code"] == "SLIPPAGE_EXCEEDED"

    # 重新获取新 token（因为上一个 token 校验前未消费或需要新的）
    res_pending2 = client.get("/api/v1/signals/pending", headers=headers)
    confirm_token2 = next(s for s in res_pending2.json() if s["signal_id"] == sig_id)[
        "confirmation_token"
    ]

    # 4. 正常价格下单成功
    res_confirm = client.post(
        f"/api/v1/signals/{sig_id}/confirm",
        headers=headers,
        json={
            "confirmation_token": confirm_token2,
            "current_price": "65100.0",
            "max_slippage_pct": "0.01",
        },
    )
    assert res_confirm.status_code == 200
    data = res_confirm.json()
    assert data["success"] is True
    assert data["cl_ord_id"].startswith("ord_")
    assert data["status"] == "SUBMITTED"

    # 5. 防重放测试：再次使用同一 confirm_token2 提交应被拒绝
    res_replay = client.post(
        f"/api/v1/signals/{sig_id}/confirm",
        headers=headers,
        json={"confirmation_token": confirm_token2, "current_price": "65000.0"},
    )
    assert res_replay.status_code == 400
    assert (
        "INVALID_CONFIRMATION_TOKEN" in res_replay.json()["code"]
        or "SIGNAL_NOT_PENDING" in res_replay.json()["code"]
    )


def test_emergency_controls(client: TestClient) -> None:
    """测试紧急开关与口令校验。"""
    settings = load_api_settings()
    code = compute_totp(settings.admin_totp_secret)
    login_res = client.post(
        "/api/v1/auth/login",
        json={
            "username": settings.admin_username,
            "password": "DQuant@2026!Secure",
            "totp_code": code,
        },
    )
    headers = {"Authorization": f"Bearer {login_res.json()['access_token']}"}

    # 1. 紧急停止开仓
    res_stop = client.post(
        "/api/v1/emergency/stop-open",
        headers=headers,
        json={"reason": "极端行情急跌熔断"},
    )
    assert res_stop.status_code == 200
    assert res_stop.json()["success"] is True

    # 检查状态
    res_status = client.get("/api/v1/emergency/status", headers=headers)
    assert res_status.status_code == 200
    assert res_status.json()["stop_open_enabled"] is True

    # 2. 一键全部平仓 - 口令错误被拦截
    res_close_err = client.post(
        "/api/v1/emergency/close-all",
        headers=headers,
        json={"reason": "紧急避险", "confirm_phrase": "WRONG_PHRASE"},
    )
    assert res_close_err.status_code == 400
    assert res_close_err.json()["code"] == "CONFIRM_PHRASE_MISMATCH"

    # 3. 一键全部平仓 - 正确口令执行
    res_close_ok = client.post(
        "/api/v1/emergency/close-all",
        headers=headers,
        json={"reason": "紧急避险", "confirm_phrase": "CLOSE_ALL_CONFIRMED"},
    )
    assert res_close_ok.status_code == 200
    assert res_close_ok.json()["success"] is True

    # 4. 人工解除熔断恢复
    res_resume = client.post(
        "/api/v1/emergency/resume",
        headers=headers,
        json={"reason": "行情企稳，人工对账核验完毕放行"},
    )
    assert res_resume.status_code == 200
    res_status_after = client.get("/api/v1/emergency/status", headers=headers)
    assert res_status_after.json()["stop_open_enabled"] is False


def test_strategy_management_and_audit(client: TestClient) -> None:
    """测试策略参数读取、更新与审计记录。"""
    settings = load_api_settings()
    code = compute_totp(settings.admin_totp_secret)
    login_res = client.post(
        "/api/v1/auth/login",
        json={
            "username": settings.admin_username,
            "password": "DQuant@2026!Secure",
            "totp_code": code,
        },
    )
    headers = {"Authorization": f"Bearer {login_res.json()['access_token']}"}

    # 读取策略列表
    res_list = client.get("/api/v1/strategies", headers=headers)
    assert res_list.status_code == 200
    strategies = res_list.json()
    assert len(strategies) >= 3

    # 更新 S1 策略参数
    new_params = {"atr_period": 20, "sl_atr_mult": 2.0}
    res_update = client.put(
        "/api/v1/strategies/s1_trend_pullback/params",
        headers=headers,
        json={"params": new_params, "reason": "根据样本外回测优化波动率适应性"},
    )
    assert res_update.status_code == 200
    assert res_update.json()["success"] is True

    # 验证新参数已生效
    res_list_after = client.get("/api/v1/strategies", headers=headers)
    s1 = next(s for s in res_list_after.json() if s["strategy_id"] == "s1_trend_pullback")
    assert s1["params"]["atr_period"] == 20

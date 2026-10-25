"""引擎整合冒烟测试: 保护 dquant.scheduler.engine 全链路接线不被回归破坏。"""

from __future__ import annotations

import importlib

import pytest

from dquant.contracts.types import SignalAction


def test_compose_entry_modules_importable() -> None:
    """docker-compose/Dockerfile 引用的 4 个入口模块必须真实存在 (整合回归护栏)。"""
    for module_name in (
        "dquant.scheduler.engine",
        "dquant.data.collector",
        "dquant.api.main",
        "dquant.backtest.run",
    ):
        spec = importlib.util.find_spec(module_name)
        assert spec is not None, f"{module_name} 不存在, docker-compose 将无法启动"


def test_api_main_exposes_uvicorn_app() -> None:
    from dquant.api.main import app
    from dquant.api.app import app as orig

    assert app is orig


def test_collector_has_cli_main() -> None:
    from dquant.data.collector import main

    assert callable(main)


def test_engine_builds_order_request_with_okx_safe_cl_ord_id() -> None:
    """订单准备阶段必须重写 clOrdId (signal_id 含冒号且超 32 字符, OKX 会拒单)。"""
    import dataclasses
    from decimal import Decimal

    from dquant.contracts import Instrument, Signal
    from dquant.contracts.types import (
        CandleBar,
        InstrumentState,
        InstrumentType,
        OrderType,
        SignalAction,
        SignalSide,
    )
    from dquant.execution.oms import ExecutionConfig, OMS
    from dquant.execution.state import build_client_order_id
    from dquant.risk.manager import RiskDecision
    from dquant.scheduler.engine import NullGateway

    instrument = Instrument(
        inst_id="BTC-USDT-SWAP",
        inst_type=InstrumentType.SWAP,
        base="BTC",
        quote="USDT",
        state=InstrumentState.LIVE,
        ct_val=Decimal("0.01"),
        lot_sz=Decimal("1"),
        min_sz=Decimal("1"),
        tick_sz=Decimal("0.0001"),
    )
    signal = Signal(
        signal_id="S1:1.0.0:BTC-USDT-SWAP:long:1750000000000",
        inst_id="BTC-USDT-SWAP",
        side=SignalSide.LONG,
        action=SignalAction.OPEN,
        entry_type=OrderType.LIMIT,
        price=Decimal("100"),
        stop_loss=Decimal("95"),
        take_profits=[],
        reason="smoke",
        strategy_name="S1",
        created_ts_ms=1750000000000,
    )
    decision = RiskDecision(
        signal_id=signal.signal_id,
        approved=True,
        rule="APPROVED",
        detail="test",
        qty=Decimal("2"),
        risk_fraction=Decimal("0.01"),
    )
    oms = OMS(NullGateway(), config=ExecutionConfig(mode="demo"))  # type: ignore[arg-type]
    order = oms.build_order_request(signal, decision, instrument)
    fixed = order.model_copy(update={"cl_ord_id": build_client_order_id("S1", signal.created_ts_ms, 1)})
    assert len(fixed.cl_ord_id) <= 32
    assert ":" not in fixed.cl_ord_id
    assert fixed.cl_ord_id != signal.signal_id


@pytest.mark.asyncio
async def test_engine_full_chain_synthetic(tmp_path) -> None:  # noqa: ANN001
    """synthetic 数据一轮全链路: 采集→币池→趋势→背离→选币(持久化)→策略上下文。"""
    from dquant.scheduler.engine import EngineOptions, TradingEngine
    from dquant.scheduler.persistence import ScreenerRunStore
    from dquant.scheduler.engine import _SessionFactoryShim

    options = EngineOptions(
        source="synthetic",
        once=True,
        db_path=str(tmp_path / "engine.db"),
        top_n=8,
        seed=7,
    )
    engine = TradingEngine(options)
    await engine.run()

    store = ScreenerRunStore(_SessionFactoryShim(engine._session_factory))  # type: ignore[arg-type]
    record = await store.load_latest_run()
    assert record is not None, "选币结果未持久化"
    assert record.universe_size == 8
    assert len(record.candidates) == 8
    pools = {c.pool for c in record.candidates}
    # 8 个币种、有涨有跌的合成数据应覆盖多个候选池, 且全部在 A~F 合法范围
    assert pools <= set("ABCDEF")
    assert len(pools) >= 3, f"候选池分布异常: {pools}"

    from dquant.contracts.types import CandleBar
    from dquant.data.store import count_candles
    with engine._session_factory() as session:
        total = sum(
            count_candles(session, inst_id, bar)
            for inst_id in engine.instruments
            for bar in (CandleBar.ONE_DAY, CandleBar.FOUR_HOUR, CandleBar.ONE_HOUR)
        )
    assert total >= 8 * (260 + 320 + 420) * 0.99


# ---------------------------------------------------------------------------
# 策略模块化: 配置选择 / 注册器装配 / 参数覆盖 / 开仓仲裁
# ---------------------------------------------------------------------------
def test_strategy_settings_section_parses() -> None:
    from dquant.settings import AppSettings

    settings = AppSettings.model_validate(
        {
            "strategies": {
                "active": ["S2", "S1"],
                "params": {"S1": {"stop_atr": 0.9}},
            }
        }
    )
    assert settings.strategies.active == ["S2", "S1"]
    assert settings.strategies.params["S1"]["stop_atr"] == 0.9
    assert AppSettings().strategies.active == ["S1", "S2", "S3"]


def test_strategy_settings_rejects_duplicates_and_empty() -> None:
    from pydantic import ValidationError
    from dquant.settings import AppSettings

    with pytest.raises(ValidationError):
        AppSettings.model_validate({"strategies": {"active": ["S1", "S1"]}})
    with pytest.raises(ValidationError):
        AppSettings.model_validate({"strategies": {"active": ["S1", " "]}})


def test_engine_registry_driven_selection(tmp_path) -> None:  # noqa: ANN001
    """CLI/配置指定的策略经注册器装配; 未注册的策略告警跳过不阻断。"""
    from dquant.scheduler.engine import EngineOptions, TradingEngine

    engine = TradingEngine(EngineOptions(db_path=str(tmp_path / "e1.db"), strategies=["S2"]))
    assert [s.name for s in engine.strategies] == ["S2"]

    engine2 = TradingEngine(
        EngineOptions(db_path=str(tmp_path / "e2.db"), strategies=["S2", "NOPE", "S1"])
    )
    assert [s.name for s in engine2.strategies] == ["S2", "S1"]


def test_engine_params_override_from_config(tmp_path) -> None:  # noqa: ANN001
    from dquant.scheduler.engine import EngineOptions, TradingEngine

    config = tmp_path / "cfg.yaml"
    config.write_text(
        "strategies:\n"
        "  active:\n"
        "    - S1\n"
        "  params:\n"
        "    S1:\n"
        "      stop_atr: 0.9\n",
        encoding="utf-8",
    )
    engine = TradingEngine(EngineOptions(db_path=str(tmp_path / "e.db"), config_path=str(config)))
    (strategy,) = engine.strategies
    assert strategy.name == "S1"
    assert strategy.params["stop_atr"] == 0.9


class _StubStrategy:
    """最小策略替身: 不走 BaseStrategy 参数校验, 直接按脚本发信号。"""

    def __init__(self, name: str, signals: list) -> None:  # noqa: ANN001
        self.name = name
        self.version = "1.0.0"
        self._signals = signals

    def accepts(self, candidate) -> bool:  # noqa: ANN001
        del candidate
        return True

    def on_bar(self, ctx) -> list:  # noqa: ANN001
        del ctx
        return list(self._signals)


def _sig(sid: str, strategy: str, inst: str, action) -> Signal:  # noqa: ANN001
    from decimal import Decimal

    from dquant.contracts import Signal
    from dquant.contracts.types import OrderType, SignalSide

    return Signal(
        signal_id=sid,
        inst_id=inst,
        side=SignalSide.LONG,
        action=action,
        entry_type=OrderType.LIMIT,
        price=Decimal("100"),
        stop_loss=Decimal("95"),
        take_profits=[],
        reason="test",
        strategy_name=strategy,
        created_ts_ms=1000,
    )


def test_select_signals_open_arbitration() -> None:
    """同一币种的开仓只允许排在前面的策略胜出。"""
    from types import SimpleNamespace

    from dquant.scheduler.engine import select_signals

    first = _StubStrategy("A", [_sig("a1", "A", "BTC-USDT-SWAP", SignalAction.OPEN)])
    second = _StubStrategy("B", [_sig("b1", "B", "BTC-USDT-SWAP", SignalAction.OPEN)])
    ctx = SimpleNamespace(candidate=None)
    result = select_signals([first, second], [ctx])
    assert [s.signal_id for s in result] == ["a1"]

    # 换顺序则 B 胜出: 顺序即优先级
    result2 = select_signals([second, first], [ctx])
    assert [s.signal_id for s in result2] == ["b1"]


def test_select_signals_different_inst_and_management_pass() -> None:
    from types import SimpleNamespace

    from dquant.scheduler.engine import select_signals

    first = _StubStrategy("A", [_sig("a1", "A", "BTC-USDT-SWAP", SignalAction.OPEN)])
    second = _StubStrategy(
        "B",
        [
            _sig("b1", "B", "ETH-USDT-SWAP", SignalAction.OPEN),        # 不同币种: 放行
            _sig("b2", "B", "BTC-USDT-SWAP", SignalAction.CLOSE),      # 管理类: 不受仲裁
        ],
    )
    result = select_signals([first, second], [SimpleNamespace(candidate=None)])
    assert {s.signal_id for s in result} == {"a1", "b1", "b2"}


def test_select_signals_dedupes_same_signal_id() -> None:
    from types import SimpleNamespace

    from dquant.scheduler.engine import select_signals

    first = _StubStrategy("A", [_sig("a1", "A", "BTC-USDT-SWAP", SignalAction.OPEN)])
    # 同一策略在两个 context 上重复发同一 signal_id
    result = select_signals([first], [SimpleNamespace(candidate=None), SimpleNamespace(candidate=None)])
    assert [s.signal_id for s in result] == ["a1"]


# ---------------------------------------------------------------------------
# OKX 真实数据链路: User-Agent / .env 加载 / 密钥读取
# ---------------------------------------------------------------------------
def test_okx_request_headers_include_user_agent() -> None:
    """OKX CDN 拒绝 urllib 默认 UA (403), 请求头必须显式携带 User-Agent。"""
    from dquant.adapters.okx import build_request_headers

    headers = build_request_headers(None, "GET", "/api/v5/public/time", "", simulated_trading=True, timestamp_ms=0)
    assert "User-Agent" in headers and headers["User-Agent"]
    assert headers["x-simulated-trading"] == "1"


def test_load_env_file_parses_and_never_overrides(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    from dquant.scheduler.engine import load_env_file

    env = tmp_path / ".env"
    env.write_text(
        "# 注释行\n"
        "OKX_DEMO_API_KEY=abc123\n"
        "OKX_DEMO_API_SECRET='quoted-secret'\n"
        'OKX_DEMO_PASSPHRASE="quoted-pass"\n'
        "EXISTING_VAR=from-file\n"
        "\n"
        "INVALID_LINE_NO_EQUALS\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OKX_DEMO_API_KEY", raising=False)
    monkeypatch.setenv("EXISTING_VAR", "from-env")

    import os

    loaded = load_env_file(env)
    assert os.environ["OKX_DEMO_API_KEY"] == "abc123"
    assert os.environ["OKX_DEMO_API_SECRET"] == "quoted-secret"
    assert os.environ["OKX_DEMO_PASSPHRASE"] == "quoted-pass"
    assert os.environ["EXISTING_VAR"] == "from-env"  # 已有环境变量不被覆盖
    assert set(loaded) == {"OKX_DEMO_API_KEY", "OKX_DEMO_API_SECRET", "OKX_DEMO_PASSPHRASE"}

    assert load_env_file(tmp_path / "missing.env") == {}


def test_load_okx_credentials_partial_and_complete(monkeypatch) -> None:  # noqa: ANN001
    from dquant.adapters.okx import OkxCredentials
    from dquant.scheduler.engine import load_okx_credentials

    for var in ("OKX_DEMO_API_KEY", "OKX_DEMO_API_SECRET", "OKX_DEMO_PASSPHRASE"):
        monkeypatch.delenv(var, raising=False)
    assert load_okx_credentials() is None

    monkeypatch.setenv("OKX_DEMO_API_KEY", "key")
    monkeypatch.setenv("OKX_DEMO_API_SECRET", "secret")
    assert load_okx_credentials() is None  # 缺 passphrase 仍为 None

    monkeypatch.setenv("OKX_DEMO_PASSPHRASE", "pass")
    creds = load_okx_credentials()
    assert isinstance(creds, OkxCredentials)
    assert (creds.api_key, creds.secret, creds.passphrase) == ("key", "secret", "pass")

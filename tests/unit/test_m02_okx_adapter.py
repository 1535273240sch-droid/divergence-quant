"""OKX 适配器测试：解析、错误分类、重试、限流、demo 强制与签名。"""

import base64
from decimal import Decimal

import pytest
from m02_helpers import FakeOkxTransport, load_fixture, run

from dquant.adapters import (
    ExchangeError,
    OkxAdapter,
    OkxCredentials,
    UrllibTransport,
    build_request_headers,
    build_signature,
    iso_timestamp_ms,
)
from dquant.contracts import (
    Candle,
    CandleBar,
    Instrument,
    InstrumentState,
    InstrumentType,
    Position,
    Ticker,
)

CANDLES_PATH = "/api/v5/market/candles"
INSTRUMENTS_PATH = "/api/v5/public/instruments"
TICKERS_PATH = "/api/v5/market/tickers"
TIME_PATH = "/api/v5/public/time"
_TEST_CREDENTIALS = OkxCredentials(api_key="key", secret="secret", passphrase="pass")


def make_adapter(transport: FakeOkxTransport, **kwargs: object) -> OkxAdapter:
    return OkxAdapter(transport, mode="demo", rate_per_sec=1000.0, **kwargs)


# ---- 抽象接口与公共契约兼容性 ----


def test_adapter_outputs_are_public_contract_models() -> None:
    transport = FakeOkxTransport()
    transport.route("GET", INSTRUMENTS_PATH, load_fixture("okx_instruments.json"))
    transport.route("GET", TICKERS_PATH, load_fixture("okx_tickers.json"))
    transport.route("GET", CANDLES_PATH, load_fixture("okx_candles.json"))
    adapter = make_adapter(transport)

    instruments = run(adapter.load_instruments(InstrumentType.SWAP))
    tickers = run(adapter.fetch_tickers(InstrumentType.SWAP))
    candles = run(adapter.fetch_candles("BTC-USDT-SWAP", CandleBar.FOUR_HOUR, None, 3))

    assert isinstance(instruments[0], Instrument)
    assert isinstance(tickers[0], Ticker)
    assert isinstance(candles[0], Candle)


# ---- 交易对解析 ----


def test_load_instruments_maps_states_and_skips_unknown_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = FakeOkxTransport()
    transport.route("GET", INSTRUMENTS_PATH, load_fixture("okx_instruments.json"))
    adapter = make_adapter(transport)

    with caplog.at_level("WARNING", logger="dquant.adapters"):
        instruments = run(adapter.load_instruments(InstrumentType.SWAP))

    # preopen 状态未收录，跳过并告警；live/suspend 正常映射。
    assert [i.inst_id for i in instruments] == ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    assert instruments[0].state is InstrumentState.LIVE
    assert instruments[1].state is InstrumentState.SUSPENDED
    assert instruments[0].ct_val == Decimal("0.01")
    assert instruments[0].base == "BTC"
    assert instruments[0].quote == "USDT"
    assert any("XRP-USDT-SWAP" in record.message for record in caplog.records)


def test_load_instruments_rejects_invalid_numeric_field() -> None:
    transport = FakeOkxTransport()
    transport.route(
        "GET",
        INSTRUMENTS_PATH,
        {
            "code": "0",
            "data": [
                {
                    "instId": "BAD-USDT-SWAP",
                    "instType": "SWAP",
                    "state": "live",
                    "ctVal": "-1",
                    "lotSz": "1",
                    "minSz": "1",
                    "tickSz": "0.1",
                    "listTime": "",
                }
            ],
        },
    )
    adapter = make_adapter(transport)

    with pytest.raises(ExchangeError, match="PARSE_ERROR"):
        run(adapter.load_instruments(InstrumentType.SWAP))


# ---- 行情与 K 线解析 ----


def test_fetch_tickers_converts_swap_quote_volume() -> None:
    transport = FakeOkxTransport()
    transport.route("GET", TICKERS_PATH, load_fixture("okx_tickers.json"))
    adapter = make_adapter(transport)

    tickers = run(adapter.fetch_tickers(InstrumentType.SWAP))

    btc, eth = tickers
    # SWAP 的 USDT 计价成交量 = volCcy24h × last：1234.56 × 67500.5。
    assert btc.quote_volume_24h == Decimal("1234.56") * Decimal("67500.5")
    assert btc.last == Decimal("67500.5")
    assert eth.bid is None and eth.ask is None
    assert btc.ts_ms == 1729716000000


def test_fetch_candles_sorts_ascending_and_maps_confirm() -> None:
    transport = FakeOkxTransport()
    transport.route("GET", CANDLES_PATH, load_fixture("okx_candles.json"))
    adapter = make_adapter(transport)

    candles = run(adapter.fetch_candles("BTC-USDT-SWAP", CandleBar.FOUR_HOUR, None, 3))

    ts_list = [c.ts_ms for c in candles]
    assert ts_list == sorted(ts_list)
    # OKX 返回最新在前且 confirm=0 表示未收盘；适配器输出升序并保留标志。
    assert [c.confirmed for c in candles] == [False, True, True]
    assert candles[0].close == Decimal("67100")
    assert candles[0].volume == Decimal("678.9")
    assert candles[2].quote_volume == Decimal("83334177000")


def test_fetch_candles_passes_inst_id_and_since_as_before_param() -> None:
    transport = FakeOkxTransport()
    transport.route("GET", CANDLES_PATH, {"code": "0", "data": []})
    adapter = make_adapter(transport)

    run(adapter.fetch_candles("BTC-USDT-SWAP", CandleBar.ONE_HOUR, 1729701600000, 100))

    method, path, params, auth = transport.requests[0]
    assert (method, path, auth) == ("GET", CANDLES_PATH, False)
    assert params["instId"] == "BTC-USDT-SWAP"
    assert params["bar"] == "1H"
    assert params["before"] == "1729701600000"
    assert params["limit"] == "100"


def test_fetch_candles_limit_bounds() -> None:
    transport = FakeOkxTransport()
    adapter = make_adapter(transport)

    with pytest.raises(ValueError, match="limit"):
        run(adapter.fetch_candles("BTC-USDT-SWAP", CandleBar.ONE_HOUR, None, 0))
    with pytest.raises(ValueError, match="limit"):
        run(adapter.fetch_candles("BTC-USDT-SWAP", CandleBar.ONE_HOUR, None, 301))


def test_fetch_history_candles_sends_after_and_before() -> None:
    transport = FakeOkxTransport()
    transport.route("GET", "/api/v5/market/history-candles", {"code": "0", "data": []})
    adapter = make_adapter(transport)

    run(
        adapter.fetch_history_candles(
            "BTC-USDT-SWAP",
            CandleBar.ONE_DAY,
            older_than_ms=1000,
            newer_than_ms=2000,
            limit=100,
        )
    )

    params = transport.requests[0][2]
    assert params["after"] == "1000"
    assert params["before"] == "2000"


def test_parse_candle_rejects_invalid_ohlc() -> None:
    transport = FakeOkxTransport()
    transport.route(
        "GET",
        CANDLES_PATH,
        {"code": "0", "data": [["1", "100", "90", "80", "95", "1", "1", "100", "1"]]},
    )
    adapter = make_adapter(transport)

    with pytest.raises(ExchangeError, match="PARSE_ERROR"):
        run(adapter.fetch_candles("BTC-USDT-SWAP", CandleBar.ONE_HOUR, None, 1))


# ---- 信封校验与错误分类 ----


def test_okx_rate_limit_code_is_retriable() -> None:
    transport = FakeOkxTransport()
    transport.route(
        "GET", CANDLES_PATH, {"code": "50011", "msg": "达到接口调用频率上限", "data": []}
    )
    adapter = make_adapter(transport)

    with pytest.raises(ExchangeError) as exc_info:
        run(adapter.fetch_candles("BTC-USDT-SWAP", CandleBar.ONE_HOUR, None, 1))

    assert exc_info.value.code == "50011"
    assert exc_info.value.retriable is True


def test_unknown_error_code_is_not_retriable() -> None:
    transport = FakeOkxTransport()
    transport.route("GET", CANDLES_PATH, {"code": "51000", "msg": "参数错误", "data": []})
    adapter = make_adapter(transport)

    with pytest.raises(ExchangeError) as exc_info:
        run(adapter.fetch_candles("BTC-USDT-SWAP", CandleBar.ONE_HOUR, None, 1))

    assert exc_info.value.retriable is False


def test_missing_envelope_fields_raise_parse_error() -> None:
    transport = FakeOkxTransport()
    transport.route("GET", CANDLES_PATH, {"unexpected": True})
    adapter = make_adapter(transport)

    with pytest.raises(ExchangeError, match="信封"):
        run(adapter.fetch_candles("BTC-USDT-SWAP", CandleBar.ONE_HOUR, None, 1))


def test_retriable_error_is_retried_inside_adapter() -> None:
    transport = FakeOkxTransport()
    transport.route("GET", CANDLES_PATH, {"code": "50011", "msg": "限频", "data": []})
    adapter = make_adapter(transport, max_attempts=3, base_delay_sec=0.01)

    with pytest.raises(ExchangeError, match="50011"):
        run(adapter.fetch_candles("BTC-USDT-SWAP", CandleBar.ONE_HOUR, None, 1))

    # 首次 + 2 次重试 = 3 次请求。
    assert len(transport.requests) == 3


# ---- demo 强制与时间同步 ----


def test_adapter_rejects_non_demo_mode() -> None:
    transport = FakeOkxTransport()
    for mode in ("live", "backtest"):
        with pytest.raises(ExchangeError, match="demo"):
            OkxAdapter(transport, mode=mode)  # type: ignore[arg-type]


def test_sync_time_returns_offset_and_warns_on_large_drift(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = FakeOkxTransport()
    transport.route("GET", TIME_PATH, {"code": "0", "data": [{"ts": "17297160005000"}]})
    adapter = make_adapter(transport)

    with caplog.at_level("WARNING", logger="dquant.adapters"):
        offset = run(adapter.sync_time())

    assert offset > 1000
    assert any("偏差" in record.message for record in caplog.records)


def test_private_endpoint_without_credentials_fails_fast() -> None:
    transport = FakeOkxTransport()
    adapter = make_adapter(transport)

    with pytest.raises(ExchangeError, match="NO_CREDENTIALS"):
        run(adapter.fetch_balance())


# ---- 账户解析 ----


def test_fetch_balance_parses_details() -> None:
    transport = FakeOkxTransport(_TEST_CREDENTIALS)
    transport.route(
        "GET",
        "/api/v5/account/balance",
        {
            "code": "0",
            "data": [
                {
                    "totalEq": "985.5",
                    "details": [
                        {"ccy": "USDT", "eq": "985.5", "availEq": "960", "frozenBal": "25.5"}
                    ],
                }
            ],
        },
    )
    adapter = make_adapter(transport)

    balance = run(adapter.fetch_balance())

    assert balance.total_equity_usd == Decimal("985.5")
    assert balance.details["USDT"].available == Decimal("960")
    assert balance.details["USDT"].frozen == Decimal("25.5")


def test_fetch_positions_filters_zero_and_maps_fields() -> None:
    transport = FakeOkxTransport(_TEST_CREDENTIALS)
    transport.route(
        "GET",
        "/api/v5/account/positions",
        {
            "code": "0",
            "data": [
                {
                    "instId": "BTC-USDT-SWAP",
                    "posSide": "long",
                    "pos": "2",
                    "avgPx": "67000",
                    "markPx": "67500",
                    "upl": "10",
                    "cTime": "1729701600000",
                },
                {"instId": "ETH-USDT-SWAP", "posSide": "short", "pos": "0"},
            ],
        },
    )
    adapter = make_adapter(transport)

    positions = run(adapter.fetch_positions())

    assert len(positions) == 1
    pos = positions[0]
    assert isinstance(pos, Position)
    assert pos.pos_side == "long"
    assert pos.size == Decimal("2")
    assert pos.strategy == "external"
    assert pos.opened_ts_ms == 1729701600000


def test_fetch_positions_rejects_net_mode() -> None:
    transport = FakeOkxTransport(_TEST_CREDENTIALS)
    transport.route(
        "GET",
        "/api/v5/account/positions",
        {
            "code": "0",
            "data": [
                {
                    "instId": "BTC-USDT-SWAP",
                    "posSide": "net",
                    "pos": "2",
                    "avgPx": "67000",
                    "markPx": "67500",
                    "upl": "10",
                    "cTime": "1729701600000",
                }
            ],
        },
    )
    adapter = make_adapter(transport)

    with pytest.raises(ExchangeError, match="双向持仓"):
        run(adapter.fetch_positions())


def test_fetch_open_orders_parses_snapshot() -> None:
    transport = FakeOkxTransport(_TEST_CREDENTIALS)
    transport.route(
        "GET",
        "/api/v5/trade/orders-pending",
        {
            "code": "0",
            "data": [
                {
                    "ordId": "623456",
                    "clOrdId": "cli-1",
                    "instId": "BTC-USDT-SWAP",
                    "side": "buy",
                    "posSide": "long",
                    "ordType": "limit",
                    "px": "67000",
                    "sz": "1",
                    "state": "live",
                }
            ],
        },
    )
    adapter = make_adapter(transport)

    orders = run(adapter.fetch_open_orders())

    assert orders[0].ord_id == "623456"
    assert orders[0].cl_ord_id == "cli-1"
    assert orders[0].side == "buy"
    assert orders[0].price == Decimal("67000")


def test_fetch_funding_rate_parses_fields() -> None:
    transport = FakeOkxTransport()
    transport.route(
        "GET",
        "/api/v5/public/funding-rate",
        {
            "code": "0",
            "data": [
                {
                    "instId": "BTC-USDT-SWAP",
                    "fundingRate": "0.000123",
                    "fundingTime": "1729718400000",
                    "nextFundingTime": "1729747200000",
                }
            ],
        },
    )
    adapter = make_adapter(transport)

    rate = run(adapter.fetch_funding_rate("BTC-USDT-SWAP"))

    assert rate.funding_rate == Decimal("0.000123")
    assert rate.next_funding_time_ms == 1729747200000


# ---- 交易域方法显式未实现 ----


def test_trade_methods_are_not_implemented_in_m02() -> None:
    transport = FakeOkxTransport()
    adapter = make_adapter(transport)

    for coro in (
        adapter.place_order(None),  # type: ignore[arg-type]
        adapter.cancel_order("BTC-USDT-SWAP", "cli-1"),
        adapter.set_leverage("BTC-USDT-SWAP", 3, "isolated"),
        adapter.subscribe_candles(["BTC-USDT-SWAP"], CandleBar.ONE_HOUR, None),  # type: ignore[arg-type]
        adapter.subscribe_orders(None),  # type: ignore[arg-type]
    ):
        with pytest.raises(NotImplementedError):
            run(coro)


# ---- 签名与请求头 ----


def test_iso_timestamp_format() -> None:
    assert iso_timestamp_ms(1700000000123) == "2023-11-14T22:13:20.123Z"


def test_build_signature_is_deterministic_base64() -> None:
    signature = build_signature(
        "test-secret",
        "2026-10-24T00:00:00.000Z",
        "GET",
        "/api/v5/account/balance?instId=BTC-USDT-SWAP",
        "",
    )
    assert signature == "kGhLSiY8Goe2WDDqSnHDHvcPUK3AVBsZPT8JcZ4isZU="
    base64.b64decode(signature, validate=True)


def test_request_headers_include_simulated_trading_and_credentials() -> None:
    credentials = OkxCredentials(api_key="key", secret="test-secret", passphrase="pass")

    headers = build_request_headers(
        credentials,
        "GET",
        "/api/v5/account/balance?instId=BTC-USDT-SWAP",
        "",
        simulated_trading=True,
        timestamp_ms=1792800000000,
    )

    assert headers["x-simulated-trading"] == "1"
    assert headers["OK-ACCESS-KEY"] == "key"
    assert headers["OK-ACCESS-PASSPHRASE"] == "pass"
    assert headers["OK-ACCESS-TIMESTAMP"] == "2026-10-24T00:00:00.000Z"
    assert headers["OK-ACCESS-SIGN"] == "kGhLSiY8Goe2WDDqSnHDHvcPUK3AVBsZPT8JcZ4isZU="


def test_request_headers_without_credentials_still_mark_simulated() -> None:
    headers = build_request_headers(
        None, "GET", "/api/v5/market/tickers", "", simulated_trading=True, timestamp_ms=1
    )
    assert headers["x-simulated-trading"] == "1"
    assert not any(key.startswith("OK-ACCESS") for key in headers)


def test_urllib_transport_requires_https() -> None:
    with pytest.raises(ValueError, match="https"):
        UrllibTransport("http://www.okx.com")

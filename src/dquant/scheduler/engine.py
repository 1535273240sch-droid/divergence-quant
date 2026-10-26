"""DivergenceQuant 全链路主进程 (engine).

组装 M02~M09 的常驻运行入口：
行情采集 → 币池初筛 → 趋势分类 → 背离检测 → 选币 → 策略信号 → 风控裁决 → (OMS 订单准备)

用法:
    python -m dquant.scheduler.engine --source synthetic --once   # 离线冒烟
    python -m dquant.scheduler.engine --source okx                # OKX 公开行情(无需 Key)
    python -m dquant.scheduler.engine --source okx --interval 30

说明:
- 默认 dry-run: 只生成风控决策与订单请求日志, 绝不下单。
- --source synthetic 使用确定性随机游走 K 线, 用于无网络环境验证全链路接线。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import os
import random
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import dquant.strategies  # noqa: F401  导入即注册全部内置策略
from dquant.adapters.base import ExchangeAdapter
from dquant.contracts import Candle, Instrument, Ticker
from dquant.contracts.types import CandleBar, InstrumentState, InstrumentType, SignalAction
from dquant.core.divergence import detect_divergences
from dquant.core.indicators import atr as atr_series
from dquant.core.indicators import sma as sma_series
from dquant.core.regime import InsufficientCandlesError, classify_regime
from dquant.core.screener import ScreenerConfig, ScreenerInputs, derive_divergence_id
from dquant.core.universe import UniverseConfig, screen_universe
from dquant.data.candles import utc_now_ms
from dquant.data.collector import AsyncAdapterRunner, update_incremental
from dquant.data.store import load_candles_between, upsert_candles
from dquant.db.models import Base, RiskDecisionModel, StrategySignalModel
from dquant.execution.okx_gateway import OkxGateway
from dquant.execution.oms import OMS, ExecutionConfig
from dquant.execution.ports import BalanceSnapshot, OrderRequest, ProtectionRequest
from dquant.execution.reconcile import Reconciler
from dquant.execution.state import build_client_order_id
from dquant.risk.killswitch import KillSwitch
from dquant.risk.manager import MarketRiskContext, RiskManager, RiskRequest
from dquant.scheduler.persistence import ScreenerRunStore
from dquant.scheduler.runner import ScreenerScheduler, bar_period_ms, last_closed_bar_ts
from dquant.scheduler.service import ScreenerService
from dquant.security import CredentialStore, CredentialStoreError
from dquant.settings import AppSettings, load_settings
from dquant.strategies import create_strategy, list_strategies
from dquant.strategies.base import BaseStrategy, StrategyContext

logger = logging.getLogger("dquant.engine")

OKX_PUBLIC_BASE_URL = "https://www.okx.com"
DEFAULT_DB_PATH = "engine.db"

# OKX 模拟盘凭证的环境变量名 (.env.example 同名)
OKX_DEMO_KEY_ENV = "OKX_DEMO_API_KEY"
OKX_DEMO_SECRET_ENV = "OKX_DEMO_API_SECRET"
OKX_DEMO_PASSPHRASE_ENV = "OKX_DEMO_PASSPHRASE"


def load_env_file(path: str | Path = ".env") -> dict[str, str]:
    """读取 .env 键值对并注入环境变量 (已存在的环境变量不覆盖)。

    行格式 KEY=VALUE, 支持 # 注释与成对引号; 无第三方依赖。
    """
    env_path = Path(path)
    if not env_path.is_file():
        return {}
    loaded: dict[str, str] = {}
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded


def load_okx_credentials() -> OkxCredentials | None:  # noqa: F821
    """优先从环境变量读取，否则读取 API 写入的加密凭证文件。"""
    from dquant.adapters.okx import OkxCredentials

    api_key = os.environ.get(OKX_DEMO_KEY_ENV, "").strip()
    secret = os.environ.get(OKX_DEMO_SECRET_ENV, "").strip()
    passphrase = os.environ.get(OKX_DEMO_PASSPHRASE_ENV, "").strip()
    if not (api_key and secret and passphrase):
        try:
            return CredentialStore().load()
        except CredentialStoreError:
            return None
    return OkxCredentials(api_key=api_key, secret=secret, passphrase=passphrase)

SYNTH_UNIVERSE = (
    "BTC-USDT-SWAP",
    "ETH-USDT-SWAP",
    "SOL-USDT-SWAP",
    "BNB-USDT-SWAP",
    "XRP-USDT-SWAP",
    "DOGE-USDT-SWAP",
    "ADA-USDT-SWAP",
    "AVAX-USDT-SWAP",
)

# 各周期需要的历史根数 (SMA200 预热 + 分类器 220 根下限 + 缓冲)
_BARS_NEEDED = {CandleBar.ONE_DAY: 260, CandleBar.FOUR_HOUR: 320, CandleBar.ONE_HOUR: 420}


# ---------------------------------------------------------------------------
# 同步 Session 包装成 ScreenerRunStore 需要的异步协议 (单引擎, 避免 SQLite 双引擎锁)
# ---------------------------------------------------------------------------
class _AsyncSessionShim:
    def __init__(self, sync_session: Session) -> None:
        self._s = sync_session

    async def execute(self, statement):  # noqa: ANN001
        return self._s.execute(statement)

    async def merge(self, instance):  # noqa: ANN001
        return self._s.merge(instance)

    def add_all(self, instances) -> None:  # noqa: ANN001
        self._s.add_all(instances)

    async def flush(self) -> None:
        self._s.flush()

    async def commit(self) -> None:
        self._s.commit()

    async def rollback(self) -> None:
        self._s.rollback()


class _SessionFactoryShim:
    """可复用的 session 上下文工厂 (同一时刻只有一个在途事务)。"""

    def __init__(self, sync_factory: sessionmaker) -> None:
        self._factory = sync_factory

    def __call__(self) -> _SessionFactoryShim:
        return self

    async def __aenter__(self) -> _AsyncSessionShim:
        self._session = self._factory()
        return _AsyncSessionShim(self._session)

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._session.close()


class NullGateway:
    """dry-run 网关: 允许构造 OMS 与 build_order_request, 但任何真实调用都会报错。"""

    async def place_order(self, request: OrderRequest):  # noqa: ANN201
        raise RuntimeError("dry-run 模式禁止真实下单")

    async def cancel_order(self, inst_id: str, cl_ord_id: str) -> None:
        raise RuntimeError("dry-run 模式禁止撤单")

    async def place_protection_order(self, request: ProtectionRequest) -> str:
        raise RuntimeError("dry-run 模式禁止保护单")

    async def fetch_open_orders(self) -> list:
        return []

    async def fetch_order(self, cl_ord_id: str):  # noqa: ANN202
        return None

    async def fetch_positions(self) -> list:
        return []

    async def fetch_balance(self) -> BalanceSnapshot:
        return BalanceSnapshot(equity=Decimal("0"), available=Decimal("0"), upl=Decimal("0"))

    async def set_leverage(self, inst_id: str, lever: int, mgn_mode: str) -> None:
        return None

    async def set_position_mode(self, mode: str) -> None:
        return None


# ---------------------------------------------------------------------------
# 确定性合成行情 (无网络冒烟用)
# ---------------------------------------------------------------------------
def _synth_candles(
    inst_id: str, bar: CandleBar, count: int, end_ts: int, seed: int
) -> list[Candle]:
    rng = random.Random(f"{inst_id}:{bar.value}:{seed}")
    period = bar_period_ms(bar)
    start_ts = end_ts - period * (count - 1)
    price = 100.0
    out: list[Candle] = []
    drift = rng.uniform(-0.002, 0.002)
    for i in range(count):
        ts = start_ts + period * i
        shock = rng.gauss(drift, 0.012)
        price = max(1.0, price * math.exp(shock))
        hi = price * (1 + abs(rng.gauss(0, 0.004)))
        lo = price * (1 - abs(rng.gauss(0, 0.004)))
        vol = rng.uniform(100, 5000)
        out.append(
            Candle(
                inst_id=inst_id,
                bar=bar,
                ts_ms=ts,
                open=Decimal(str(round(lo, 8))),
                high=Decimal(str(round(hi, 8))),
                low=Decimal(str(round(lo, 8))),
                close=Decimal(str(round(price, 8))),
                volume=Decimal(str(round(vol, 4))),
                quote_volume=Decimal(str(round(vol * price, 4))),
                confirmed=True,
            )
        )
    return out


def _synth_instruments() -> list[Instrument]:
    return [
        Instrument(
            inst_id=inst_id,
            inst_type=InstrumentType.SWAP,
            base=inst_id.split("-")[0],
            quote="USDT",
            state=InstrumentState.LIVE,
            ct_val=Decimal("0.01"),
            lot_sz=Decimal("1"),
            min_sz=Decimal("1"),
            tick_sz=Decimal("0.0001"),
        )
        for inst_id in SYNTH_UNIVERSE
    ]


def _synth_tickers() -> list[Ticker]:
    rng = random.Random(42)
    out: list[Ticker] = []
    for inst_id in SYNTH_UNIVERSE:
        last = Decimal(str(round(rng.uniform(0.1, 60000), 4)))
        out.append(
            Ticker(
                inst_id=inst_id,
                last=last,
                bid=None,
                ask=None,
                quote_volume_24h=Decimal(str(rng.uniform(25_000_000, 800_000_000))),
                ts_ms=utc_now_ms(),
            )
        )
    return out


# ---------------------------------------------------------------------------
# 策略分发与开仓仲裁
# ---------------------------------------------------------------------------
def select_signals(
    strategies: Sequence[BaseStrategy],
    contexts: Sequence[StrategyContext],
    log: logging.Logger | None = None,
) -> list:
    """按 active 顺序分发策略并执行开仓仲裁。

    规则:
    1. 同一 signal_id 只保留首个 (幂等去重);
    2. 同一币种每根 K 线只允许一个策略的开仓信号胜出——先到先得,
       顺序即 strategies 列表顺序 (配置 strategies.active 的书写顺序);
    3. 平仓/减仓/移动止损等管理类信号不受仲裁限制 (风控与 OMS 自行幂等)。
    """
    log = log or logger
    claimed: dict[str, str] = {}
    seen_ids: set[str] = set()
    signals: list = []
    for strategy in strategies:
        for ctx in contexts:
            if not strategy.accepts(ctx.candidate):
                continue
            for sig in strategy.on_bar(ctx):
                if sig.signal_id in seen_ids:
                    continue
                seen_ids.add(sig.signal_id)
                if sig.action is SignalAction.OPEN:
                    owner = claimed.get(sig.inst_id)
                    if owner is not None and owner != sig.strategy_name:
                        log.info(
                            "开仓仲裁: %s 在 %s 上的开仓让位给 %s",
                            sig.strategy_name, sig.inst_id, owner,
                        )
                        continue
                    claimed.setdefault(sig.inst_id, sig.strategy_name)
                signals.append(sig)
    return signals


# ---------------------------------------------------------------------------
# 主引擎
# ---------------------------------------------------------------------------
@dataclass
class EngineOptions:
    source: str = "synthetic"          # synthetic | okx
    dry_run: bool = True
    db_path: str = DEFAULT_DB_PATH
    once: bool = False
    interval_sec: float = 30.0
    config_path: str | None = None
    top_n: int = 8
    seed: int = 7
    strategies: Sequence[str] | None = None  # 覆盖 config 的 strategies.active


class TradingEngine:
    def __init__(self, options: EngineOptions) -> None:
        self.options = options
        load_env_file()  # .env 中的密钥/连接串注入环境 (不覆盖已有变量)
        self.settings: AppSettings = self._load_settings()
        self._sync_engine = create_engine(f"sqlite:///{options.db_path}", future=True)
        Base.metadata.create_all(self._sync_engine)
        self._session_factory = sessionmaker(self._sync_engine, future=True)
        self._screener_store = ScreenerRunStore(_SessionFactoryShim(self._session_factory))  # type: ignore[arg-type]

        self.instruments: dict[str, Instrument] = {i.inst_id: i for i in _synth_instruments()}
        self.kill_switch = KillSwitch()
        self.risk_manager = RiskManager(instruments=self.instruments, kill_switch=self.kill_switch)
        self.strategies: list[BaseStrategy] = self._build_strategies()
        self.oms: OMS = OMS(NullGateway(), config=ExecutionConfig(mode="demo"))  # type: ignore[arg-type]
        self._adapter: ExchangeAdapter | None = None
        self._gateway: OkxGateway | None = None
        self._last_inputs: ScreenerInputs | None = None
        self._seq = 0

    # -- 初始化 ------------------------------------------------------------
    def _load_settings(self) -> AppSettings:
        path = self.options.config_path or os.environ.get(
            "DQUANT_CONFIG_PATH", "config/config.yaml"
        )
        if Path(path).exists():
            return load_settings(path)
        return load_settings()

    def _build_strategies(self) -> list[BaseStrategy]:
        """按配置(CLI 覆盖 > config.yaml)从注册器装配策略模块。

        - active 列表顺序即同币种开仓冲突时的优先级
        - 未注册的策略名告警并跳过, 不阻断引擎启动
        - params 按策略名覆盖构造参数; 策略自身 enabled 参数仍最优先
        """
        active = list(self.options.strategies or self.settings.strategies.active)
        params_map = self.settings.strategies.params
        known = set(list_strategies())
        built: list[BaseStrategy] = []
        for name in active:
            if name not in known:
                logger.warning("策略 %s 未注册(可用: %s), 已跳过", name, sorted(known))
                continue
            built.append(create_strategy(name, params_map.get(name) or None))
        if not built:
            logger.warning("active 策略列表为空, 引擎将只选币不产生交易信号")
        logger.info(
            "策略装配: active=%s loaded=%s",
            active,
            [s.name for s in built],
        )
        return built

    async def _init_okx(self) -> None:
        from dquant.adapters.okx import OkxAdapter, UrllibTransport

        creds = load_okx_credentials()
        if creds is None and os.getenv("DQUANT_REQUIRE_PRIVATE_API", "NO").upper() == "YES":
            raise RuntimeError(
                "DQUANT_REQUIRE_PRIVATE_API=YES，但 OKX_DEMO_API_KEY/SECRET/PASSPHRASE 未完整配置"
            )
        if creds is not None:
            logger.info("OKX 模拟盘密钥已加载 (私有接口可用: 余额/持仓/订单)")
        else:
            logger.info(
                "未检测到 OKX 密钥 (%s/%s/%s), 仅使用公开行情接口 (K线/Ticker 无需密钥)",
                OKX_DEMO_KEY_ENV, OKX_DEMO_SECRET_ENV, OKX_DEMO_PASSPHRASE_ENV,
            )
        transport = UrllibTransport(OKX_PUBLIC_BASE_URL, creds, simulated_trading=True)
        adapter = OkxAdapter(transport, mode="demo")
        transport.clock_offset_ms = await adapter.sync_time()
        self._adapter = adapter
        if creds is not None:
            self._gateway = OkxGateway(adapter)
            self.oms = OMS(
                self._gateway,
                config=ExecutionConfig(mode="demo", require_recovery=True),
            )
            try:
                report = await self.oms.recover(Reconciler(self._gateway, self.oms.store))
                logger.info(
                    "OKX 启动对账完成: open_orders=%d positions=%d unresolved=%d safe=%s",
                    len(report.adopted_remote_orders),
                    report.position_count,
                    len(report.unresolved_orders),
                    report.safe_to_resume,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("OKX 启动对账失败，已保持开仓锁定: %s", exc)
            try:
                balance = await adapter.fetch_balance()
                logger.info("密钥验证通过, 模拟盘账户权益: %s USDT", balance.total_equity_usd)
            except Exception as exc:  # noqa: BLE001
                logger.warning("密钥验证失败 (行情采集不受影响): %s", exc)

    async def initialize(self) -> None:
        if self.options.source == "okx":
            await self._init_okx()
            instruments = await self._adapter.load_instruments(InstrumentType.SWAP)  # type: ignore[union-attr]
            self.instruments = {i.inst_id: i for i in instruments if i.quote == "USDT"}
            self.risk_manager = RiskManager(
                instruments=self.instruments,
                kill_switch=self.kill_switch,
            )
        logger.info(
            "engine 初始化完成 source=%s universe=%d dry_run=%s",
            self.options.source,
            len(self.instruments),
            self.options.dry_run,
        )

    # -- 行情采集 ------------------------------------------------------------
    async def collect_once(self) -> int:
        now = utc_now_ms()
        if self.options.source == "synthetic":
            end_1h = last_closed_bar_ts(now, CandleBar.ONE_HOUR)
            candles: list[Candle] = []
            for inst_id in self.instruments:
                for bar, count in _BARS_NEEDED.items():
                    end_ts = min(last_closed_bar_ts(now, bar), end_1h)
                    if bar is CandleBar.ONE_DAY:
                        # 日线必须对齐北京时间 00:00 (UTC 16:00), 见 data/candles.py 质检规则
                        end_ts -= (end_ts - 57_600_000) % 86_400_000
                    candles.extend(_synth_candles(inst_id, bar, count, end_ts, self.options.seed))
            with self._session_factory() as session:
                written = 0
                for i in range(0, len(candles), 100):  # SQLite 单语句变量上限, 分批 upsert
                    written += upsert_candles(session, candles[i : i + 100])
                session.commit()
            logger.info("synthetic 采集完成: %d 根 K 线", written)
            return written

        adapter = self._adapter
        assert adapter is not None
        tickers = await adapter.fetch_tickers(InstrumentType.SWAP)
        # 采集前预筛: 历史根数尚未入库(首轮全为 0), 以 1 占位放行, 仅按成交额/状态/计价币排序;
        # 真正带历史根数门槛的筛选发生在数据落库后的 _build_inputs_sync。
        history = {inst_id: 1 for inst_id in self.instruments}
        selected = screen_universe(
            list(self.instruments.values()), tickers, history,
            config=UniverseConfig(top_n_by_volume=self.options.top_n, min_history_bars=1),
        )
        inst_ids = selected.selected_inst_ids
        bars = [CandleBar.ONE_DAY, CandleBar.FOUR_HOUR, CandleBar.ONE_HOUR]
        written = 0
        for inst_id in inst_ids:
            for bar in bars:
                lookback = _BARS_NEEDED[bar] * bar_period_ms(bar)

                def _one(
                    inst_id: str = inst_id,
                    bar: CandleBar = bar,
                    lookback: int = lookback,
                ) -> int:
                    runner = AsyncAdapterRunner(adapter)  # type: ignore[arg-type]
                    with self._session_factory() as session:
                        stats = update_incremental(
                            runner,
                            session,
                            inst_id,
                            bar,
                            lookback_ms=lookback,
                        )
                        session.commit()
                        return stats.upserted_rows

                written += await asyncio.to_thread(_one)
        logger.info("okx 采集完成: %d 行写入, universe=%d", written, len(inst_ids))
        return written

    # -- 输入装配 (M05 InputProvider 的生产实现) ----------------------------
    def _build_inputs_sync(self, close_ts_ms: int) -> ScreenerInputs:
        session = self._session_factory()
        try:
            regimes = []
            divergences = []
            history_bars: dict[str, int] = {}
            for inst_id in self.instruments:
                daily = [
                    c
                    for c in load_candles_between(
                        session, inst_id, CandleBar.ONE_DAY, 0, close_ts_ms - 1
                    )
                    if c.confirmed
                ]
                h4 = [
                    c
                    for c in load_candles_between(
                        session, inst_id, CandleBar.FOUR_HOUR, 0, close_ts_ms - 1
                    )
                    if c.confirmed
                ]
                history_bars[inst_id] = len(daily)
                if len(daily) >= 220:
                    try:
                        regimes.append(classify_regime(inst_id, CandleBar.ONE_DAY, daily))
                    except (InsufficientCandlesError, ValueError) as exc:
                        logger.debug("趋势分类跳过 %s: %s", inst_id, exc)
                if len(h4) >= 60:
                    for sig in detect_divergences(h4):
                        if sig.preliminary or sig.confirmed_ts >= close_ts_ms:
                            continue
                        divergences.append(sig)
            selected = screen_universe(
                list(self.instruments.values()),
                self._current_tickers,
                history_bars,
                config=UniverseConfig(
                    top_n_by_volume=self.options.top_n,
                    min_history_bars=max(1, min(200, min(history_bars.values() or [1]))),
                ),
            )
            keep = set(selected.selected_inst_ids)
            keep.add("BTC-USDT-SWAP")
            regimes = [r for r in regimes if r.inst_id in keep]
            divergences = [d for d in divergences if d.inst_id in keep]
            liquidity = {
                t.inst_id: t.quote_volume_24h
                for t in self._current_tickers
                if t.inst_id in keep
            }
            return ScreenerInputs(regimes=regimes, divergences=divergences, liquidity=liquidity)
        finally:
            session.close()

    @property
    def _current_tickers(self) -> list[Ticker]:
        if self.options.source == "synthetic" or self._adapter is None:
            return _synth_tickers()
        # 本属性只在 asyncio.to_thread 的 worker 线程里被调用, 可安全 asyncio.run
        try:
            return _fetch_tickers_blocking(self._adapter)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ticker 拉取失败, 使用空列表: %s", exc)
            return []

    async def _provide_inputs(self) -> ScreenerInputs:
        close_ts = last_closed_bar_ts(utc_now_ms(), CandleBar.FOUR_HOUR)
        inputs = await asyncio.to_thread(self._build_inputs_sync, close_ts)
        self._last_inputs = inputs
        return inputs

    # -- 策略与风控 ------------------------------------------------------------
    def _build_context(self, candidate) -> StrategyContext | None:  # noqa: ANN001
        session = self._session_factory()
        try:
            inst_id = candidate.inst_id
            now = utc_now_ms()
            h1 = [
                c
                for c in load_candles_between(session, inst_id, CandleBar.ONE_HOUR, 0, now)
                if c.confirmed
            ]
            h4 = [
                c
                for c in load_candles_between(session, inst_id, CandleBar.FOUR_HOUR, 0, now)
                if c.confirmed
            ]
            daily = [
                c
                for c in load_candles_between(session, inst_id, CandleBar.ONE_DAY, 0, now)
                if c.confirmed
            ]
            if len(h1) < 210 or len(h4) < 50 or len(daily) < 2:
                return None
            highs = [float(c.high) for c in h1]
            lows = [float(c.low) for c in h1]
            closes = [float(c.close) for c in h1]
            atr_last = Decimal(str(round(float(atr_series(highs, lows, closes, 14)[-1]), 8)))
            if atr_last <= 0:
                return None
            ma200_1h = sma_series(closes, 200)
            ma200_val = (
                None
                if math.isnan(ma200_1h[-1])
                else Decimal(str(round(float(ma200_1h[-1]), 8)))
            )
            daily_closes = [float(c.close) for c in daily]
            daily_ma200 = sma_series(daily_closes, 200)
            daily_ma200_val = (
                None
                if math.isnan(daily_ma200[-1])
                else Decimal(str(round(float(daily_ma200[-1]), 8)))
            )
            btc_regime = None
            if inst_id != "BTC-USDT-SWAP":
                btc_daily = [
                    c
                    for c in load_candles_between(
                        session, "BTC-USDT-SWAP", CandleBar.ONE_DAY, 0, now
                    )
                    if c.confirmed
                ]
                if len(btc_daily) >= 220:
                    try:
                        btc_regime = classify_regime(
                            "BTC-USDT-SWAP", CandleBar.ONE_DAY, btc_daily
                        ).state
                    except (InsufficientCandlesError, ValueError):
                        btc_regime = None
            now_close = h1[-1].ts_ms + bar_period_ms(CandleBar.ONE_HOUR)
            divergence = self._divergence_for(candidate)
            return StrategyContext(
                candidate=candidate,
                divergence=divergence,
                trigger_candles=tuple(h1[-6:]),
                atr=atr_last,
                range_candles=tuple(h4[-50:]),
                ma200=ma200_val,
                btc_regime=btc_regime,
                position=None,
                daily_close=Decimal(str(round(daily_closes[-1], 8))),
                daily_ma200=daily_ma200_val,
                now_ts_ms=now_close,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("构建 StrategyContext 失败 %s: %s", candidate.inst_id, exc)
            return None
        finally:
            session.close()

    def _divergence_for(self, candidate):  # noqa: ANN001, ANN202
        inputs = self._last_inputs
        if inputs is None:
            return None
        wanted = candidate.divergence_id
        if not wanted:
            return None
        for div in inputs.divergences:
            if derive_divergence_id(div) == wanted:
                return div
        return None

    async def strategy_and_risk_cycle(self, record) -> None:  # noqa: ANN001
        inputs = self._last_inputs
        if inputs is None:
            return
        contexts: list[StrategyContext] = []
        for cand in record.candidates:
            ctx = self._build_context(cand)
            if ctx is not None:
                contexts.append(ctx)
        signals = select_signals(self.strategies, contexts)
        logger.info(
            "策略周期: candidates=%d contexts=%d signals=%d strategies=%s",
            len(record.candidates), len(contexts), len(signals),
            [s.name for s in self.strategies],
        )
        equity = Decimal("10000")
        for signal in signals:
            request = RiskRequest(
                signal=signal,
                equity=equity,
                instrument=self.instruments.get(signal.inst_id),
                positions=(),
                pending_orders=(),
                ts_ms=signal.created_ts_ms,
                daily_pnl=Decimal("0"),
                weekly_pnl=Decimal("0"),
                market=MarketRiskContext(),
            )
            decision = self.risk_manager.evaluate(request)
            logger.info(
                "风控裁决 %s %s %s approved=%s rule=%s qty=%s",
                signal.strategy_name, signal.inst_id, signal.action.value,
                decision.approved, decision.rule, decision.qty,
            )
            self._persist_signal(signal, decision)
            if decision.approved and decision.qty > 0 and not self.options.dry_run:
                await self._submit_order(signal, decision)

    def _persist_signal(self, signal, decision) -> None:  # noqa: ANN001
        """把策略信号与风控裁决落库，供 API / 前端读取（幂等 upsert）。"""

        created_at = datetime.fromtimestamp(signal.created_ts_ms / 1000, tz=UTC)
        try:
            with self._session_factory() as session:
                session.merge(
                    StrategySignalModel(
                        id=signal.signal_id,
                        strategy=signal.strategy_name,
                        inst_id=signal.inst_id,
                        side=signal.side.value,
                        action=signal.action.value,
                        price=signal.price,
                        stop_loss=signal.stop_loss,
                        take_profits=[
                            {"price": str(tp.price), "fraction": str(tp.fraction)}
                            for tp in signal.take_profits
                        ],
                        reason=signal.reason,
                        status="PENDING" if decision.approved else "REJECTED",
                        created_at=created_at,
                    )
                )
                session.merge(
                    RiskDecisionModel(
                        id=f"{signal.signal_id}:risk",
                        signal_id=signal.signal_id,
                        approved=decision.approved,
                        rule=decision.rule,
                        detail=decision.detail,
                        qty=decision.qty,
                        created_at=created_at,
                    )
                )
                session.commit()
        except Exception as exc:  # noqa: BLE001 - 落库失败不得中断交易主链路
            logger.warning("信号落库失败 %s: %s", signal.signal_id, exc)

    async def _submit_order(self, signal, decision) -> None:  # noqa: ANN001
        instrument = self.instruments[signal.inst_id]
        order = self.oms.build_order_request(signal, decision, instrument)
        self._seq += 1
        prefix = signal.strategy_name.upper()[:8]
        order = order.model_copy(
            update={"cl_ord_id": build_client_order_id(prefix, signal.created_ts_ms, self._seq)}
        )
        if self._gateway is None:
            logger.warning("未配置 OKX 私有凭证，订单仅生成未提交: %s", order.cl_ord_id)
            return
        ack = await self.oms.submit(order)
        logger.info(
            "订单已提交 OKX: clOrdId=%s ordId=%s state=%s qty=%s",
            ack.cl_ord_id,
            ack.ord_id,
            ack.state.value,
            order.size,
        )

    # -- 主循环 ------------------------------------------------------------
    async def run(self) -> None:
        await self.initialize()
        service = ScreenerService(
            store=self._screener_store,
            input_provider=self._provide_inputs,
            config=ScreenerConfig(),
        )
        scheduler = ScreenerScheduler(service, bar=CandleBar.FOUR_HOUR)
        await self.collect_once()
        if self.options.once:
            record = await scheduler.run_manual()
            logger.info(
                "手动选币完成 run_id=%s universe=%d candidates=%d",
                record.run_id, record.universe_size, len(record.candidates),
            )
            await self.strategy_and_risk_cycle(record)
            return
        logger.info("进入常驻循环 interval=%.0fs (Ctrl+C 退出)", self.options.interval_sec)
        last_run_id: str | None = None
        while True:
            try:
                record = await scheduler.poll(utc_now_ms())
                if record is not None and record.run_id != last_run_id:
                    last_run_id = record.run_id
                    logger.info(
                        "选币完成 run_id=%s candidates=%d",
                        record.run_id,
                        len(record.candidates),
                    )
                    await self.strategy_and_risk_cycle(record)
                    await self.collect_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("调度周期失败, 将在下一轮重试")
            await asyncio.sleep(self.options.interval_sec)


def _fetch_tickers_blocking(adapter) -> list[Ticker]:  # noqa: ANN001
    """在 worker 线程里自建事件循环拉取 ticker (供同步装配上下文使用)。"""
    import asyncio as _aio

    return _aio.run(adapter.fetch_tickers(InstrumentType.SWAP))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dquant.scheduler.engine",
        description="DivergenceQuant 全链路主进程",
    )
    parser.add_argument("--source", choices=["synthetic", "okx"], default="synthetic")
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    parser.add_argument("--config", default=None)
    parser.add_argument("--top-n", type=int, default=8)
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--once", action="store_true", help="只跑一轮选币+策略+风控后退出")
    parser.add_argument(
        "--strategies",
        default=None,
        help="覆盖 config 的策略选择, 逗号分隔, 如: S1 或 S1,S2",
    )
    parser.add_argument("--execute", action="store_true", help="解除 dry-run (仍受 OMS/网关限制)")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    options = EngineOptions(
        source=args.source,
        dry_run=not args.execute,
        db_path=args.db,
        once=args.once,
        interval_sec=args.interval,
        config_path=args.config,
        top_n=args.top_n,
        seed=args.seed,
        strategies=(
            [name.strip() for name in args.strategies.split(",")]
            if args.strategies
            else None
        ),
    )
    engine = TradingEngine(options)
    try:
        asyncio.run(engine.run())
    except KeyboardInterrupt:
        logger.info("收到退出信号")
    return 0


if __name__ == "__main__":
    sys.exit(main())

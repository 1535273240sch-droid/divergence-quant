"""DivergenceQuant 初始数据库模型。

模型覆盖计划书第 7 章的持久化边界。M01 只负责结构和字段语义，业务写入逻辑由后续模块负责。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class InstrumentModel(Base):
    __tablename__ = "instruments"

    inst_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    inst_type: Mapped[str] = mapped_column(String(16), nullable=False)
    base: Mapped[str] = mapped_column(String(32), nullable=False)
    quote: Mapped[str] = mapped_column(String(16), nullable=False)
    ct_val: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    lot_sz: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    min_sz: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    tick_sz: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    list_time_ms: Mapped[int | None] = mapped_column(BigInteger)


class CandleModel(Base):
    __tablename__ = "candles"

    inst_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    bar: Mapped[str] = mapped_column(String(8), primary_key=True)
    ts_ms: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    open: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    vol: Mapped[Decimal] = mapped_column(Numeric(40, 12), nullable=False)
    vol_quote: Mapped[Decimal] = mapped_column(Numeric(40, 12), nullable=False)
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class RegimeSnapshotModel(Base):
    __tablename__ = "regime_snapshots"

    inst_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    bar: Mapped[str] = mapped_column(String(8), primary_key=True)
    ts_ms: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ma_type: Mapped[str] = mapped_column(String(8), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    dist: Mapped[float] = mapped_column(Float, nullable=False)
    slope: Mapped[float] = mapped_column(Float, nullable=False)
    above_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    cross_count: Mapped[int] = mapped_column(Integer, nullable=False)
    bars_in_state: Mapped[int] = mapped_column(Integer, nullable=False)


class DivergenceSignalModel(Base):
    __tablename__ = "divergence_signals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    inst_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    bar: Mapped[str] = mapped_column(String(8), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    method: Mapped[str] = mapped_column(String(24), nullable=False)
    seg1_start_ts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    seg1_end_ts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    seg1_extreme_ts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    seg1_extreme_price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    seg1_peak: Mapped[float] = mapped_column(Float, nullable=False)
    seg1_area: Mapped[float] = mapped_column(Float, nullable=False)
    seg1_avg_vol: Mapped[float] = mapped_column(Float, nullable=False)
    seg2_start_ts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    seg2_end_ts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    seg2_extreme_ts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    seg2_extreme_price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    seg2_peak: Mapped[float] = mapped_column(Float, nullable=False)
    seg2_area: Mapped[float] = mapped_column(Float, nullable=False)
    seg2_avg_vol: Mapped[float] = mapped_column(Float, nullable=False)
    height_div: Mapped[bool] = mapped_column(Boolean, nullable=False)
    area_div: Mapped[bool] = mapped_column(Boolean, nullable=False)
    volume_confirm: Mapped[bool] = mapped_column(Boolean, nullable=False)
    close_confirm: Mapped[bool] = mapped_column(Boolean, nullable=False)
    divergence_order: Mapped[int] = mapped_column(Integer, nullable=False)
    confirmed_ts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    preliminary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ScreenerRunModel(Base):
    __tablename__ = "screener_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    universe_size: Mapped[int] = mapped_column(Integer, nullable=False)
    params_hash: Mapped[str] = mapped_column(String(128), nullable=False)


class ScreenerResultModel(Base):
    __tablename__ = "screener_results"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    inst_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pool: Mapped[str] = mapped_column(String(1), nullable=False)
    regime: Mapped[str] = mapped_column(String(16), nullable=False)
    divergence_id: Mapped[str | None] = mapped_column(String(64))
    score: Mapped[float] = mapped_column(Float, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)


class StrategySignalModel(Base):
    __tablename__ = "strategy_signals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    inst_id: Mapped[str] = mapped_column(String(64), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    stop_loss: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    take_profits: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False, default=list)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RiskDecisionModel(Base):
    __tablename__ = "risk_decisions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    signal_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rule: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(40, 12), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OrderModel(Base):
    __tablename__ = "orders"

    cl_ord_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ord_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    inst_id: Mapped[str] = mapped_column(String(64), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    pos_side: Mapped[str] = mapped_column(String(8), nullable=False)
    order_type: Mapped[str] = mapped_column(String(16), nullable=False)
    price: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    size: Mapped[Decimal] = mapped_column(Numeric(40, 12), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FillModel(Base):
    __tablename__ = "fills"

    trade_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    cl_ord_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    size: Mapped[Decimal] = mapped_column(Numeric(40, 12), nullable=False)
    fee: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)


class PositionModel(Base):
    __tablename__ = "positions"

    inst_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pos_side: Mapped[str] = mapped_column(String(8), primary_key=True)
    size: Mapped[Decimal] = mapped_column(Numeric(40, 12), nullable=False)
    avg_px: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    upl: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    stop_loss: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EquitySnapshotModel(Base):
    __tablename__ = "equity_snapshots"

    ts_ms: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    equity: Mapped[Decimal] = mapped_column(Numeric(40, 12), nullable=False)
    available: Mapped[Decimal] = mapped_column(Numeric(40, 12), nullable=False)
    upl: Mapped[Decimal] = mapped_column(Numeric(40, 12), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(40, 12), nullable=False)


class RiskEventModel(Base):
    __tablename__ = "risk_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rule: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)


class AuditLogModel(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    before: Mapped[dict[str, object] | None] = mapped_column(JSON)
    after: Mapped[dict[str, object] | None] = mapped_column(JSON)


class SignalConfirmationModel(Base):
    __tablename__ = "signal_confirmations"

    signal_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirm_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))


class GoldenSampleModel(Base):
    __tablename__ = "golden_samples"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    inst_id: Mapped[str] = mapped_column(String(64), nullable=False)
    bar: Mapped[str] = mapped_column(String(8), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    seg1_start_ts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    seg1_end_ts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    seg2_start_ts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    seg2_end_ts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DivergenceFeedbackModel(Base):
    __tablename__ = "divergence_feedback"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    divergence_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    params_snapshot: Mapped[dict[str, object] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SymbolCooldownModel(Base):
    __tablename__ = "symbol_cooldowns"

    inst_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    direction: Mapped[str] = mapped_column(String(8), primary_key=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AIReviewModel(Base):
    __tablename__ = "ai_reviews"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    divergence_id: Mapped[str | None] = mapped_column(String(64))
    inst_id: Mapped[str] = mapped_column(String(64), nullable=False)
    visual_score_delta: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cleanliness: Mapped[int | None] = mapped_column(Integer)
    sr_resonance: Mapped[bool | None] = mapped_column(Boolean)
    news_verdict: Mapped[str | None] = mapped_column(String(16))
    briefing_text: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    model_used: Mapped[str] = mapped_column(String(128), nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AIOptimizationProposalModel(Base):
    __tablename__ = "ai_optimization_proposals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    week_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    diagnosis_md: Mapped[str] = mapped_column(Text, nullable=False)
    params_before_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    params_after_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    in_sample_metrics: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    out_of_sample_metrics: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AICallLogModel(Base):
    __tablename__ = "ai_call_logs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)


class MacroEventModel(Base):
    __tablename__ = "macro_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    country: Mapped[str] = mapped_column(String(16), nullable=False)
    impact: Mapped[str] = mapped_column(String(8), nullable=False)
    event_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)


class NotifyOutboxModel(Base):
    __tablename__ = "notify_outbox"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    attachments: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BacktestRunModel(Base):
    __tablename__ = "backtest_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    params: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    period: Mapped[str] = mapped_column(String(128), nullable=False)
    metrics_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    report_path: Mapped[str | None] = mapped_column(Text)

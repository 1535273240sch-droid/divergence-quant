"""内部模拟盘（纸面成交）仓位存储。

MHD 策略在 dry-run 模式下使用本模块记录模拟仓位；仓位写入 SQLite 的
``positions`` 表，因此 API 的 ``/market/positions`` 和控制台可以立即读取。
所有价格、数量和盈亏均使用 ``Decimal``。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from dquant.contracts import Instrument, Position, PositionSide, Signal, SignalSide
from dquant.db.models import PositionModel
from dquant.risk.calculator import RiskInputError, calculate_fixed_fraction_size


class PaperBroker:
    """基于 SQLite ``positions`` 表的模拟盘仓位管理器。"""

    def __init__(
        self,
        session_factory: sessionmaker,
        instruments: Mapping[str, Instrument],
    ) -> None:
        self._session_factory = session_factory
        self.instruments = dict(instruments)

    # -- 查询 ---------------------------------------------------------------
    def list_positions(self) -> list[Position]:
        with self._session_factory() as session:
            rows = session.execute(select(PositionModel)).scalars().all()
            return [self._to_position(row) for row in rows]

    def get_position(self, inst_id: str, pos_side: PositionSide) -> Position | None:
        with self._session_factory() as session:
            row = session.get(PositionModel, (inst_id, pos_side.value))
            return self._to_position(row) if row is not None else None

    # -- 开仓 / 平仓 / 调仓 ------------------------------------------------
    def open_position(
        self,
        *,
        signal: Signal,
        qty: Decimal,
        price: Decimal,
        stop_loss: Decimal | None,
    ) -> Position | None:
        instrument = self.instruments.get(signal.inst_id)
        if instrument is None or qty <= 0 or price <= 0:
            return None
        pos_side = PositionSide.LONG if signal.side is SignalSide.LONG else PositionSide.SHORT
        now_ms = signal.created_ts_ms
        with self._session_factory() as session:
            row = session.get(PositionModel, (signal.inst_id, pos_side.value))
            if row is None:
                row = PositionModel(
                    inst_id=signal.inst_id,
                    pos_side=pos_side.value,
                    size=qty,
                    avg_px=price,
                    upl=Decimal("0"),
                    stop_loss=stop_loss,
                    strategy=signal.strategy_name,
                    opened_at=datetime.fromtimestamp(now_ms / 1000, tz=UTC),
                )
            else:
                # 同一方向已有仓位时按加权均价合并，避免模拟盘出现重复行。
                total = row.size + qty
                row.avg_px = (row.avg_px * row.size + price * qty) / total
                row.size = total
                row.stop_loss = stop_loss if stop_loss is not None else row.stop_loss
                row.strategy = signal.strategy_name
            session.merge(row)
            session.commit()
            return self._to_position(row)

    def close_position(
        self,
        inst_id: str,
        pos_side: PositionSide,
        price: Decimal,
    ) -> Decimal:
        instrument = self.instruments.get(inst_id)
        with self._session_factory() as session:
            row = session.get(PositionModel, (inst_id, pos_side.value))
            if row is None:
                return Decimal("0")
            if instrument is not None:
                if pos_side is PositionSide.LONG:
                    pnl = (price - row.avg_px) * row.size * instrument.ct_val
                else:
                    pnl = (row.avg_px - price) * row.size * instrument.ct_val
            else:
                pnl = Decimal("0")
            session.delete(row)
            session.commit()
            return pnl

    def update_stop(
        self,
        inst_id: str,
        pos_side: PositionSide,
        stop_loss: Decimal,
    ) -> bool:
        with self._session_factory() as session:
            row = session.get(PositionModel, (inst_id, pos_side.value))
            if row is None:
                return False
            row.stop_loss = stop_loss
            session.merge(row)
            session.commit()
            return True

    def update_mark(self, inst_id: str, pos_side: PositionSide, mark_price: Decimal) -> None:
        instrument = self.instruments.get(inst_id)
        if instrument is None:
            return
        with self._session_factory() as session:
            row = session.get(PositionModel, (inst_id, pos_side.value))
            if row is None:
                return
            if pos_side is PositionSide.LONG:
                row.upl = (mark_price - row.avg_px) * row.size * instrument.ct_val
            else:
                row.upl = (row.avg_px - mark_price) * row.size * instrument.ct_val
            session.merge(row)
            session.commit()

    def rebalance(
        self,
        *,
        inst_id: str,
        pos_side: PositionSide,
        target_size: Decimal,
        price: Decimal,
    ) -> Position | None:
        """把模拟仓位调整到目标张数；只调整本地纸面仓位，不向交易所下单。"""

        if target_size < 0:
            raise RiskInputError("target_size must not be negative")
        with self._session_factory() as session:
            row = session.get(PositionModel, (inst_id, pos_side.value))
            if row is None:
                return None
            if target_size <= 0:
                session.delete(row)
                session.commit()
                return None
            if target_size > row.size:
                delta = target_size - row.size
                row.avg_px = (row.avg_px * row.size + price * delta) / target_size
            row.size = target_size
            session.merge(row)
            session.commit()
            return self._to_position(row)

    def target_size(
        self,
        *,
        equity: Decimal,
        position_pct: Decimal,
        position_multiplier: Decimal,
        leverage: Decimal = Decimal("1"),
        price: Decimal,
        inst_id: str,
    ) -> Decimal:
        instrument = self.instruments.get(inst_id)
        if instrument is None:
            return Decimal("0")
        sizing = calculate_fixed_fraction_size(
            equity=equity,
            position_pct=position_pct,
            position_multiplier=position_multiplier,
            leverage=leverage,
            entry_price=price,
            instrument=instrument,
        )
        return sizing.qty

    @staticmethod
    def _to_position(row: PositionModel) -> Position:
        opened_at = row.opened_at
        if opened_at.tzinfo is None:
            opened_at = opened_at.replace(tzinfo=UTC)
        return Position(
            inst_id=row.inst_id,
            pos_side=PositionSide(row.pos_side),
            size=row.size,
            avg_price=row.avg_px,
            mark_price=row.avg_px,
            upl=row.upl,
            stop_loss=row.stop_loss,
            strategy=row.strategy,
            opened_ts_ms=int(opened_at.timestamp() * 1000),
        )


__all__ = ["PaperBroker"]

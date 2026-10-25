"""K 线持久化与读取（计划书 §5.2.4，表结构由 M01 的 ``candles`` 提供）。

M02 阶段使用同步 SQLAlchemy 会话：

- 补数与增量更新是批处理任务（脚本/定时任务），同步执行足够；
- 当前项目依赖里没有异步 SQLite 驱动，同步路径才能在单元测试中真实执行 SQL；
- 长驻服务的异步会话接入由后续集成批次处理，届时复用这里的语句构造逻辑。

存储层不做提交，由调用方控制事务边界；写入时强制校验周期对齐。
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import Table, func, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from dquant.contracts import Candle, CandleBar
from dquant.data.candles import validate_bar_alignment
from dquant.db.models import CandleModel

_TABLE: Table = CandleModel.__table__


def _row_from_candle(candle: Candle) -> dict[str, object]:
    return {
        "inst_id": candle.inst_id,
        "bar": candle.bar.value,
        "ts_ms": candle.ts_ms,
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "vol": candle.volume,
        "vol_quote": candle.quote_volume,
        "confirmed": candle.confirmed,
    }


def model_to_candle(model: CandleModel) -> Candle:
    """把 ORM 行转换回公共契约的 :class:`Candle`。"""

    return Candle(
        inst_id=model.inst_id,
        bar=CandleBar(model.bar),
        ts_ms=model.ts_ms,
        open=model.open,
        high=model.high,
        low=model.low,
        close=model.close,
        volume=model.vol,
        quote_volume=model.vol_quote,
        confirmed=model.confirmed,
    )


def _dedupe_rows(candles: Sequence[Candle]) -> list[dict[str, object]]:
    """按主键去重，后出现的记录覆盖先出现的（调用方按 ts 升序传入时即"新值覆盖旧值"）。"""

    merged: dict[tuple[str, str, int], dict[str, object]] = {}
    for candle in candles:
        merged[(candle.inst_id, candle.bar.value, candle.ts_ms)] = _row_from_candle(candle)
    return list(merged.values())


def build_candle_upsert(dialect_name: str, rows: Sequence[dict[str, object]]):
    """构造方言感知的多行 upsert 语句（PostgreSQL 与 SQLite 共用同一套业务去重逻辑）。"""

    if not rows:
        return None
    if dialect_name == "postgresql":
        insert = postgresql.insert(_TABLE)
    elif dialect_name == "sqlite":
        insert = sqlite.insert(_TABLE)
    else:
        raise ValueError(f"暂不支持的数据库方言: {dialect_name}")
    stmt = insert.values(list(rows))
    return stmt.on_conflict_do_update(
        index_elements=[_TABLE.c.inst_id, _TABLE.c.bar, _TABLE.c.ts_ms],
        set_={
            column: stmt.excluded[column]
            for column in ("open", "high", "low", "close", "vol", "vol_quote", "confirmed")
        },
    )


def upsert_candles(session: Session, candles: Sequence[Candle]) -> int:
    """幂等写入 K 线（主键 ``inst_id + bar + ts_ms``），返回实际写入的行数。

    写入前逐条校验周期对齐；任何一条不对齐都会整体报错，不允许脏数据落库。
    """

    if not candles:
        return 0
    for candle in candles:
        validate_bar_alignment(candle.bar, candle.ts_ms)
    rows = _dedupe_rows(candles)
    stmt = build_candle_upsert(session.bind.dialect.name, rows)
    if stmt is None:
        return 0
    session.execute(stmt)
    return len(rows)


def load_candles_between(
    session: Session,
    inst_id: str,
    bar: CandleBar,
    since_ms: int,
    until_ms: int,
) -> list[Candle]:
    """读取 ``[since_ms, until_ms]`` 闭区间内的 K 线，按时间升序返回。"""

    rows = (
        session.execute(
            select(CandleModel)
            .where(
                CandleModel.inst_id == inst_id,
                CandleModel.bar == bar.value,
                CandleModel.ts_ms >= since_ms,
                CandleModel.ts_ms <= until_ms,
            )
            .order_by(CandleModel.ts_ms)
        )
        .scalars()
        .all()
    )
    return [model_to_candle(row) for row in rows]


def load_latest_candle_ts(session: Session, inst_id: str, bar: CandleBar) -> int | None:
    """返回该交易对该周期已存储的最新时间戳；没有任何记录时返回 None。"""

    row = session.execute(
        select(CandleModel.ts_ms)
        .where(CandleModel.inst_id == inst_id, CandleModel.bar == bar.value)
        .order_by(CandleModel.ts_ms.desc())
        .limit(1)
    ).scalar_one_or_none()
    return row


def count_candles(session: Session, inst_id: str, bar: CandleBar) -> int:
    """统计已存储的 K 线行数。"""

    value = session.execute(
        select(func.count())
        .select_from(CandleModel)
        .where(CandleModel.inst_id == inst_id, CandleModel.bar == bar.value)
    ).scalar_one()
    return int(value)


def stored_candle_close(
    session: Session, inst_id: str, bar: CandleBar, ts_ms: int
) -> Decimal | None:
    """读取指定时间戳的收盘价，用于数据核对；不存在时返回 None。"""

    row = session.execute(
        select(CandleModel.close).where(
            CandleModel.inst_id == inst_id,
            CandleModel.bar == bar.value,
            CandleModel.ts_ms == ts_ms,
        )
    ).scalar_one_or_none()
    return row

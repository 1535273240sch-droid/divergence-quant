"""M05 选币运行记录的持久化边界。

负责计划书第 7 章 `screener_runs` / `screener_results` 两张表的读写。
save_run 在单个事务内先删除同 run_id 的旧结果再写入，因此同一 run_id 重复保存
是替换语义，不会产生重复运行记录或脏数据。

计划的表结构没有 trigger/close_ts 列，run_id 约定携带这些信息：
`sched-<close_ts_ms>` 为定时触发，`manual-<uuid>` 为手动触发。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import delete, select

from dquant.contracts import RegimeState, ScreenerCandidate
from dquant.core.screener import ScreenerRunRecord
from dquant.db.models import ScreenerResultModel, ScreenerRunModel

logger = logging.getLogger(__name__)

SCHEDULED_RUN_ID_PREFIX = "sched-"
MANUAL_RUN_ID_PREFIX = "manual-"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class ScreenerStoreError(Exception):
    """选币运行记录读写失败。"""


class ScreenerSession(Protocol):
    """store 需要的最小异步会话协议，兼容 SQLAlchemy AsyncSession。"""

    async def execute(self, statement: Any) -> Any: ...

    async def merge(self, instance: Any) -> Any: ...

    def add_all(self, instances: Sequence[Any]) -> None: ...

    async def flush(self) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


SessionFactory = Callable[[], AbstractAsyncContextManager[ScreenerSession]]


def scheduled_run_id(close_ts_ms: int) -> str:
    """定时触发的确定性 run_id：同一收盘重复扫描会替换同一条运行记录。"""

    return f"{SCHEDULED_RUN_ID_PREFIX}{close_ts_ms}"


def manual_run_id() -> str:
    """手动触发每次都是新的运行记录。"""

    return f"{MANUAL_RUN_ID_PREFIX}{uuid.uuid4().hex}"


def parse_run_id(run_id: str) -> tuple[str, int | None]:
    """从 run_id 还原触发类型与 4H 收盘时间戳；外部 id 返回 unknown。"""

    if run_id.startswith(SCHEDULED_RUN_ID_PREFIX):
        suffix = run_id[len(SCHEDULED_RUN_ID_PREFIX) :]
        try:
            return "scheduled", int(suffix)
        except ValueError:
            return "unknown", None
    if run_id.startswith(MANUAL_RUN_ID_PREFIX):
        return "manual", None
    return "unknown", None


def _ts_to_datetime(ts_ms: int) -> datetime:
    """UTC 毫秒时间戳转带时区的 datetime，保证落库为 UTC。"""

    return _EPOCH + timedelta(milliseconds=ts_ms)


def _datetime_to_ts(value: datetime) -> int:
    """带时区的 datetime 转 UTC 毫秒时间戳。

    PostgreSQL（asyncpg）返回带时区的 datetime；SQLite 返回 naive datetime。
    项目约定数据库一律按 UTC 存储，因此 naive 值按 UTC 解释。
    """

    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return (value - _EPOCH) // timedelta(milliseconds=1)


def run_row_from_record(record: ScreenerRunRecord) -> ScreenerRunModel:
    """运行记录 -> screener_runs 行。"""

    return ScreenerRunModel(
        run_id=record.run_id,
        started_at=_ts_to_datetime(record.started_ts_ms),
        finished_at=_ts_to_datetime(record.finished_ts_ms),
        universe_size=record.universe_size,
        params_hash=record.params_hash,
    )


def result_rows_from_record(record: ScreenerRunRecord) -> list[ScreenerResultModel]:
    """候选列表 -> screener_results 行。"""

    return [
        ScreenerResultModel(
            run_id=record.run_id,
            inst_id=candidate.inst_id,
            pool=candidate.pool,
            regime=candidate.regime.value,
            divergence_id=candidate.divergence_id,
            score=candidate.score,
            rank=candidate.rank,
        )
        for candidate in record.candidates
    ]


def record_from_rows(
    run_row: ScreenerRunModel,
    result_rows: Sequence[ScreenerResultModel],
) -> ScreenerRunRecord:
    """数据库行 -> 运行记录。

    warnings 是运行期诊断，表结构不存储，读回时恒为空。
    候选的 updated_ts_ms 同样不在表结构中，读回时用 started_at 代替。
    """

    trigger, close_ts_ms = parse_run_id(run_row.run_id)
    started_ts_ms = _datetime_to_ts(run_row.started_at)
    finished_ts_ms = (
        _datetime_to_ts(run_row.finished_at) if run_row.finished_at is not None else started_ts_ms
    )
    candidates = tuple(
        ScreenerCandidate(
            inst_id=row.inst_id,
            pool=row.pool,
            regime=RegimeState(row.regime),
            divergence_id=row.divergence_id,
            # 计划书第 7 章的 screener_results 表没有 divergence_kind 列，
            # 读回时无法还原背离类型，只能置 None；下游需要类型时应按
            # divergence_id 关联背离信号表。已在 HANDOFF 登记结构变更申请。
            divergence_kind=None,
            score=row.score,
            rank=row.rank,
            updated_ts_ms=started_ts_ms,
        )
        for row in result_rows
    )
    return ScreenerRunRecord(
        run_id=run_row.run_id,
        trigger=trigger,
        close_ts_ms=close_ts_ms,
        started_ts_ms=started_ts_ms,
        finished_ts_ms=finished_ts_ms,
        universe_size=run_row.universe_size,
        params_hash=run_row.params_hash,
        candidates=candidates,
        warnings=(),
    )


class ScreenerRunStore:
    """screener_runs / screener_results 的读写边界。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def save_run(self, record: ScreenerRunRecord) -> None:
        """原子替换式保存：同 run_id 重跑不会留下旧结果。"""

        async with self._session_factory() as session:
            try:
                await session.execute(
                    delete(ScreenerResultModel).where(ScreenerResultModel.run_id == record.run_id)
                )
                await session.merge(run_row_from_record(record))
                session.add_all(result_rows_from_record(record))
                await session.commit()
            except Exception as exc:
                await self._safe_rollback(session)
                raise ScreenerStoreError(f"failed to persist screener run {record.run_id}") from exc

    async def load_run(self, run_id: str) -> ScreenerRunRecord | None:
        """按 run_id 读取完整运行记录；不存在返回 None。"""

        async with self._session_factory() as session:
            try:
                run_row = (
                    await session.execute(
                        select(ScreenerRunModel).where(ScreenerRunModel.run_id == run_id)
                    )
                ).scalar_one_or_none()
                if run_row is None:
                    return None
                result_rows = (
                    (
                        await session.execute(
                            select(ScreenerResultModel)
                            .where(ScreenerResultModel.run_id == run_id)
                            .order_by(ScreenerResultModel.pool, ScreenerResultModel.rank)
                        )
                    )
                    .scalars()
                    .all()
                )
            except Exception as exc:
                raise ScreenerStoreError(f"failed to load screener run {run_id}") from exc
        return record_from_rows(run_row, list(result_rows))

    async def load_latest_run(self) -> ScreenerRunRecord | None:
        """读取最近一次运行（按 started_at）。"""

        async with self._session_factory() as session:
            try:
                run_row = (
                    await session.execute(
                        select(ScreenerRunModel)
                        .order_by(
                            ScreenerRunModel.started_at.desc(), ScreenerRunModel.run_id.desc()
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if run_row is None:
                    return None
                result_rows = (
                    (
                        await session.execute(
                            select(ScreenerResultModel)
                            .where(ScreenerResultModel.run_id == run_row.run_id)
                            .order_by(ScreenerResultModel.pool, ScreenerResultModel.rank)
                        )
                    )
                    .scalars()
                    .all()
                )
            except Exception as exc:
                raise ScreenerStoreError("failed to load latest screener run") from exc
        return record_from_rows(run_row, list(result_rows))

    async def load_latest_candidates(self) -> list[ScreenerCandidate]:
        """读取最近一次运行的候选，供策略/通知层消费。"""

        latest = await self.load_latest_run()
        if latest is None:
            return []
        return list(latest.candidates)

    @staticmethod
    async def _safe_rollback(session: ScreenerSession) -> None:
        """回滚失败不掩盖原始异常，但要留下告警日志。"""

        try:
            await session.rollback()
        except Exception:
            logger.warning("回滚选币运行事务失败", exc_info=True)

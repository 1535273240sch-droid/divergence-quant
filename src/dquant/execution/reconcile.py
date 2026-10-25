"""交易所与本地执行状态对账和启动恢复。"""

from __future__ import annotations

from dataclasses import dataclass

from dquant.contracts import OrderState

from .errors import InvalidOrderTransition, ReconciliationError
from .ports import ExchangeGateway, RemoteOrder
from .state import TERMINAL_STATES, transition_order_state
from .store import ExecutionStore, StoredOrder


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    """一次对账的可审计结果。"""

    adopted_remote_orders: tuple[str, ...]
    state_changes: dict[str, tuple[OrderState, OrderState]]
    unresolved_orders: tuple[str, ...]
    position_count: int
    balance_synced: bool

    @property
    def safe_to_resume(self) -> bool:
        """没有未解决订单差异时才允许恢复开仓。"""

        return not self.unresolved_orders


class Reconciler:
    """以交易所快照为准修正本地执行存储。"""

    def __init__(self, gateway: ExchangeGateway, store: ExecutionStore) -> None:
        self.gateway = gateway
        self.store = store

    async def run(self) -> ReconcileReport:
        try:
            remote_open = await self.gateway.fetch_open_orders()
            remote_positions = await self.gateway.fetch_positions()
            remote_balance = await self.gateway.fetch_balance()
        except Exception as exc:
            raise ReconciliationError("交易所对账读取失败") from exc

        remote_by_cl_ord_id = {item.request.cl_ord_id: item for item in remote_open}
        adopted: list[str] = []
        state_changes: dict[str, tuple[OrderState, OrderState]] = {}
        unresolved: list[str] = []

        for remote in remote_open:
            local = self.store.get_order(remote.request.cl_ord_id)
            if local is None:
                self.store.save_order(
                    StoredOrder(
                        request=remote.request,
                        state=remote.state,
                        ord_id=remote.ord_id,
                    )
                )
                adopted.append(remote.request.cl_ord_id)
                continue
            self._apply_remote_state(local, remote, state_changes)

        for local in self.store.list_orders():
            if local.state in TERMINAL_STATES or local.request.cl_ord_id in remote_by_cl_ord_id:
                continue
            remote = await self.gateway.fetch_order(local.request.cl_ord_id)
            if remote is None:
                unresolved.append(local.request.cl_ord_id)
                continue
            self._apply_remote_state(local, remote, state_changes)

        self.store.replace_positions(remote_positions)
        self.store.set_balance(remote_balance)
        return ReconcileReport(
            adopted_remote_orders=tuple(sorted(adopted)),
            state_changes=state_changes,
            unresolved_orders=tuple(sorted(unresolved)),
            position_count=len(remote_positions),
            balance_synced=True,
        )

    def _apply_remote_state(
        self,
        local: StoredOrder,
        remote: RemoteOrder,
        state_changes: dict[str, tuple[OrderState, OrderState]],
    ) -> None:
        if local.request != remote.request:
            raise ReconciliationError(f"订单请求与交易所快照不一致: {local.request.cl_ord_id}")
        try:
            next_state = transition_order_state(local.state, remote.state)
        except InvalidOrderTransition as exc:
            raise ReconciliationError(
                "交易所状态不能从 "
                f"{local.state.value} 迁移到 {remote.state.value}: {local.request.cl_ord_id}"
            ) from exc
        if next_state != local.state:
            state_changes[local.request.cl_ord_id] = (local.state, next_state)
        self.store.update_order(
            StoredOrder(
                request=local.request,
                state=next_state,
                ord_id=remote.ord_id,
                accepted_ts_ms=local.accepted_ts_ms,
                message="对账以交易所状态为准",
                filled_size=local.filled_size,
                protected_size=local.protected_size,
            )
        )

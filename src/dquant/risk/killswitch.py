"""停止开仓、全部平仓和熔断状态机。"""

from __future__ import annotations

from dataclasses import dataclass
from time import time


@dataclass(frozen=True, slots=True)
class KillSwitchState:
    """可持久化的紧急开关状态快照。"""

    opening_enabled: bool = True
    close_all_requested: bool = False
    circuit_breaker_active: bool = False
    reason: str | None = None
    changed_ts_ms: int | None = None


class KillSwitch:
    """区分“停止开仓”和“管理已有仓位”的状态控制器。

    熔断只能人工解除；解除熔断不会自动清除人工停止开仓或全部平仓请求。
    """

    def __init__(self) -> None:
        self._state = KillSwitchState()
        self._manual_stop = False

    @property
    def state(self) -> KillSwitchState:
        """返回当前状态快照。"""

        return self._state

    @property
    def can_open(self) -> bool:
        """当前是否允许新的开仓审核通过。"""

        return self._state.opening_enabled and not self._state.circuit_breaker_active

    def stop_opening(self, reason: str) -> KillSwitchState:
        """停止新开仓，但不要求平掉已有仓位。"""

        if not reason.strip():
            raise ValueError("reason must not be empty")
        if self._manual_stop and not self._state.opening_enabled:
            return self._state
        self._manual_stop = True
        return self._replace(opening_enabled=False, reason=reason)

    def request_close_all(self, reason: str) -> KillSwitchState:
        """请求上层执行全部平仓，同时停止新开仓。"""

        if not reason.strip():
            raise ValueError("reason must not be empty")
        self._manual_stop = True
        if self._state.close_all_requested and not self._state.opening_enabled:
            return self._state
        return self._replace(opening_enabled=False, close_all_requested=True, reason=reason)

    def trigger_circuit_breaker(self, reason: str, *, close_all: bool = False) -> KillSwitchState:
        """锁定熔断状态；最大回撤熔断通常同时请求全部平仓。"""

        if not reason.strip():
            raise ValueError("reason must not be empty")
        if self._state.circuit_breaker_active:
            return self._state
        self._manual_stop = True
        return self._replace(
            opening_enabled=False,
            close_all_requested=self._state.close_all_requested or close_all,
            circuit_breaker_active=True,
            reason=reason,
        )

    def release_circuit_breaker(self) -> KillSwitchState:
        """人工解除熔断；不会越权恢复人工停止开仓。"""

        if not self._state.circuit_breaker_active:
            return self._state
        return self._replace(
            opening_enabled=not self._manual_stop,
            circuit_breaker_active=False,
            reason="人工解除熔断",
        )

    def resume_opening(self) -> KillSwitchState:
        """人工恢复开仓；熔断尚未解除时仍保持禁止开仓。"""

        self._manual_stop = False
        if self._state.circuit_breaker_active:
            return self._replace(opening_enabled=False, reason="熔断仍处于锁定状态")
        return self._replace(opening_enabled=True, reason="人工恢复开仓")

    def clear_close_all_request(self) -> KillSwitchState:
        """清除待执行的全部平仓请求，不自动恢复开仓。"""

        if not self._state.close_all_requested:
            return self._state
        return self._replace(close_all_requested=False, reason="清除全部平仓请求")

    def _replace(self, **changes: object) -> KillSwitchState:
        values = {
            "opening_enabled": self._state.opening_enabled,
            "close_all_requested": self._state.close_all_requested,
            "circuit_breaker_active": self._state.circuit_breaker_active,
            "reason": self._state.reason,
            "changed_ts_ms": int(time() * 1000),
        }
        values.update(changes)
        self._state = KillSwitchState(**values)
        return self._state

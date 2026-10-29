"""策略运行时配置：API 写入、引擎热加载的共享 JSON 文件。

文件默认位于 ``data/strategy_runtime.json``，保存策略参数覆盖值。API 进程写入时
使用临时文件 + 原子替换，引擎通过 ``revision()`` 检测变化并重建策略实例。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

try:  # pragma: no cover - Windows 本地开发没有 fcntl，服务器/CI 使用 Linux
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

_DEFAULT_PATH = "data/strategy_runtime.json"


class StrategyRuntimeConfig:
    """线程/进程安全的最小策略运行参数存储。"""

    def __init__(self, path: str | Path | None = None) -> None:
        raw = path or os.environ.get("DQUANT_STRATEGY_RUNTIME_FILE") or _DEFAULT_PATH
        self.path = Path(raw)
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._ensure_parent()

    def _ensure_parent(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def revision(self) -> int:
        """返回可用于热加载判断的版本号；文件不存在时为 0。"""

        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return 0
        return int(stat.st_mtime_ns) ^ int(stat.st_size)

    def snapshot(self) -> dict[str, Any]:
        data = self._read()
        return {
            "version": int(data.get("version", 1)),
            "updated_ts_ms": int(data.get("updated_ts_ms", 0)),
            "active": list(data.get("active", [])),
            "strategies": {
                str(name): dict(entry.get("params", {}))
                for name, entry in dict(data.get("strategies", {})).items()
            },
        }

    def active(self, default: list[str]) -> list[str]:
        data = self._read()
        active = data.get("active")
        if isinstance(active, list) and active and all(isinstance(item, str) for item in active):
            return list(active)
        return list(default)

    def params_for(self, strategy_name: str) -> dict[str, Any]:
        entry = dict(self._read().get("strategies", {})).get(strategy_name)
        if not isinstance(entry, dict):
            return {}
        params = entry.get("params")
        return dict(params) if isinstance(params, dict) else {}

    def update(
        self,
        strategy_name: str,
        params: dict[str, Any],
        *,
        active: list[str] | None = None,
    ) -> dict[str, Any]:
        """合并写入某个策略的参数覆盖值，返回最新快照。"""

        with self._locked():
            data = self._read_unlocked()
            strategies = dict(data.get("strategies", {}))
            entry = dict(strategies.get(strategy_name, {}))
            current = dict(entry.get("params", {}))
            current.update(params)
            entry["params"] = current
            entry["updated_ts_ms"] = int(time.time() * 1000)
            strategies[strategy_name] = entry
            data["strategies"] = strategies
            if active is not None:
                data["active"] = list(active)
            data["version"] = int(data.get("version", 1))
            data["updated_ts_ms"] = int(time.time() * 1000)
            self._write_unlocked(data)
        return self.snapshot()

    def _read(self) -> dict[str, Any]:
        with self._locked():
            return self._read_unlocked()

    def _read_unlocked(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        if not raw.strip():
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        self._ensure_parent()
        fd, temp_name = tempfile.mkstemp(
            prefix=self.path.name + ".",
            suffix=".tmp",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, self.path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    class _Lock:
        def __init__(self, path: Path) -> None:
            self.path = path
            self._handle = None

        def __enter__(self) -> None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("a+")
            if fcntl is not None:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            if self._handle is not None:
                if fcntl is not None:
                    fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
                self._handle.close()
                self._handle = None

    def _locked(self) -> StrategyRuntimeConfig._Lock:
        return StrategyRuntimeConfig._Lock(self._lock_path)


__all__ = ["StrategyRuntimeConfig"]

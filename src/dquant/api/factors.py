"""因子表达式引擎与因子库。

设计约束：

1. 公式只允许对 OHLCV 做四则运算和白名单函数调用；禁止属性、下标、
   lambda、推导式、import 等任意代码执行路径。
2. 因子定义保存到 ``data/factors.db``，由前端注册与管理。
3. 引擎当前是单租户，因子为全局共享；计算只读取引擎库里的真实 K 线。
"""

from __future__ import annotations

import ast
import math
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from dquant.api import live_data

SERIES_FIELDS = {"open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4", "returns"}
MAX_FORMULA_LEN = 600
MAX_AST_NODES = 240
MAX_WINDOW = 2000


class FactorError(ValueError):
    """因子公式非法或计算失败。"""


# ---------------------------------------------------------------------------
# 存储
# ---------------------------------------------------------------------------
_DB_LOCK = threading.Lock()


def factors_db_path() -> Path:
    raw = (os.getenv("DQUANT_FACTORS_DB") or "data/factors.db").strip() or "data/factors.db"
    path = Path(raw)
    return path if path.is_absolute() else Path.cwd() / path


def _connect() -> sqlite3.Connection:
    path = factors_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS factors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            formula TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 0.5,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_by TEXT NOT NULL DEFAULT '',
            created_ts_ms INTEGER NOT NULL,
            updated_ts_ms INTEGER NOT NULL
        )
        """
    )
    return conn


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": str(row["name"]),
        "formula": str(row["formula"]),
        "weight": float(row["weight"]),
        "enabled": bool(row["enabled"]),
        "created_by": str(row["created_by"]),
        "created_ts_ms": int(row["created_ts_ms"]),
        "updated_ts_ms": int(row["updated_ts_ms"]),
    }


def list_factors(enabled_only: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM factors"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY id ASC"
    with _DB_LOCK:
        conn = _connect()
        try:
            return [_row(r) for r in conn.execute(sql).fetchall()]
        finally:
            conn.close()


def get_factor(factor_id: int) -> dict[str, Any] | None:
    with _DB_LOCK:
        conn = _connect()
        try:
            row = conn.execute("SELECT * FROM factors WHERE id = ?", (factor_id,)).fetchone()
        finally:
            conn.close()
    return _row(row) if row else None


def create_factor(
    name: str, formula: str, weight: float, created_by: str
) -> dict[str, Any]:
    _validate_meta(name, formula, weight)
    now = int(time.time() * 1000)
    with _DB_LOCK:
        conn = _connect()
        try:
            try:
                cur = conn.execute(
                    "INSERT INTO factors (name, formula, weight, enabled, created_by,"
                    " created_ts_ms, updated_ts_ms) VALUES (?, ?, ?, 1, ?, ?, ?)",
                    (name, formula, weight, created_by, now, now),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise FactorError("因子名称已存在") from exc
        finally:
            conn.close()
    created = get_factor(int(cur.lastrowid))
    if created is None:  # pragma: no cover - 极端竞态
        raise FactorError("因子创建失败")
    return created


def update_factor(
    factor_id: int,
    name: str | None = None,
    formula: str | None = None,
    weight: float | None = None,
    enabled: bool | None = None,
) -> dict[str, Any] | None:
    current = get_factor(factor_id)
    if current is None:
        return None
    new_name = name if name is not None else current["name"]
    new_formula = formula if formula is not None else current["formula"]
    new_weight = weight if weight is not None else current["weight"]
    new_enabled = enabled if enabled is not None else current["enabled"]
    _validate_meta(new_name, new_formula, new_weight)
    now = int(time.time() * 1000)
    with _DB_LOCK:
        conn = _connect()
        try:
            try:
                conn.execute(
                    "UPDATE factors SET name = ?, formula = ?, weight = ?, enabled = ?,"
                    " updated_ts_ms = ? WHERE id = ?",
                    (new_name, new_formula, new_weight, 1 if new_enabled else 0, now, factor_id),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise FactorError("因子名称已存在") from exc
        finally:
            conn.close()
    return get_factor(factor_id)


def delete_factor(factor_id: int) -> bool:
    with _DB_LOCK:
        conn = _connect()
        try:
            cur = conn.execute("DELETE FROM factors WHERE id = ?", (factor_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def _validate_meta(name: str, formula: str, weight: float) -> None:
    name = (name or "").strip()
    if not 2 <= len(name) <= 40:
        raise FactorError("因子名称需为 2–40 个字符")
    if not -1.0 <= float(weight) <= 1.0:
        raise FactorError("因子权重需在 -1 到 1 之间")
    validate_formula(formula)
    # 用合成 K 线跑一遍，提前发现参数数量/窗口类型等运行时错误。
    candles = []
    price = 100.0
    for i in range(320):
        price *= 1 + 0.002 * math.sin(i / 7.0)
        open_px = price * (1 + 0.001 * math.sin(i / 3.0))
        candles.append(
            {
                "ts_ms": i * 3_600_000,
                "open": open_px,
                "high": max(open_px, price) * 1.002,
                "low": min(open_px, price) * 0.998,
                "close": price,
                "vol": 1000.0 + i,
            }
        )
    try:
        evaluate_series(formula, candles)
    except FactorError:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一转换为可展示的错误
        raise FactorError(f"公式无法计算: {exc}") from exc


# ---------------------------------------------------------------------------
# 表达式求值
# ---------------------------------------------------------------------------
_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Pow,
    ast.UAdd,
    ast.USub,
)


def validate_formula(formula: str) -> None:
    _parse(formula)


def _parse(formula: str) -> ast.Expression:
    text = (formula or "").strip()
    if not text:
        raise FactorError("公式不能为空")
    if len(text) > MAX_FORMULA_LEN:
        raise FactorError("公式过长")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise FactorError(f"公式语法错误: {exc.msg}") from exc
    count = 0
    for node in ast.walk(tree):
        count += 1
        if count > MAX_AST_NODES:
            raise FactorError("公式过于复杂")
        if not isinstance(node, _ALLOWED_NODES):
            raise FactorError(f"不支持的语法: {type(node).__name__}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
                raise FactorError("不支持的函数调用")
            if node.keywords:
                raise FactorError("函数不支持关键字参数")
    return tree


def _num(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FactorError("参数必须是数值")
    return float(value)


def _series(value: Any, length: int) -> list[float]:
    if isinstance(value, list):
        if len(value) != length:
            raise FactorError("向量长度不一致")
        return value
    return [float(value)] * length


def _args(values: list[Any], length: int) -> list[list[float]]:
    return [_series(v, length) for v in values]


def _arg_int(value: Any) -> int:
    if isinstance(value, list):
        uniq = {int(v) for v in value if math.isfinite(v)}
        if len(uniq) != 1:
            raise FactorError("窗口参数必须是整数")
        value = uniq.pop()
    number = _num(value)
    if number != int(number) or not 1 <= int(number) <= MAX_WINDOW:
        raise FactorError("窗口参数需为 1–2000 的整数")
    return int(number)


def _map2(a: list[float], b: list[float], fn) -> list[float]:
    out: list[float] = []
    for x, y in zip(a, b):
        try:
            out.append(fn(x, y))
        except (ZeroDivisionError, ValueError, OverflowError):
            out.append(math.nan)
    return out


def _div(a: float, b: float) -> float:
    if b == 0:
        return math.nan
    return a / b


def _safe_unary(fn):
    def run(x: float) -> float:
        try:
            return fn(x)
        except (ValueError, OverflowError):
            return math.nan

    return run


def _rolling(x: list[float], n: int, fn) -> list[float]:
    out = [math.nan] * len(x)
    for i in range(n - 1, len(x)):
        window = x[i - n + 1 : i + 1]
        if any(not math.isfinite(v) for v in window):
            continue
        out[i] = fn(window)
    return out


def _mean(window: list[float]) -> float:
    return sum(window) / len(window)


def _std(window: list[float]) -> float:
    avg = _mean(window)
    return math.sqrt(sum((v - avg) ** 2 for v in window) / len(window))


def _ema(x: list[float], n: int) -> list[float]:
    alpha = 2.0 / (n + 1.0)
    out: list[float] = []
    prev = math.nan
    for value in x:
        if not math.isfinite(value):
            out.append(math.nan)
            continue
        prev = value if not math.isfinite(prev) else alpha * value + (1 - alpha) * prev
        out.append(prev)
    return out


def _rsi(x: list[float], n: int) -> list[float]:
    out = [math.nan] * len(x)
    gains = 0.0
    losses = 0.0
    for i in range(1, len(x)):
        diff = x[i] - x[i - 1]
        gain = max(diff, 0.0)
        loss = max(-diff, 0.0)
        if i <= n:
            gains += gain
            losses += loss
            if i == n:
                avg_gain = gains / n
                avg_loss = losses / n
                out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
            continue
        avg_gain = (avg_gain * (n - 1) + gain) / n
        avg_loss = (avg_loss * (n - 1) + loss) / n
        out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def _corr(a: list[float], b: list[float], n: int) -> list[float]:
    out = [math.nan] * len(a)
    for i in range(n - 1, len(a)):
        wa = a[i - n + 1 : i + 1]
        wb = b[i - n + 1 : i + 1]
        if any(not math.isfinite(v) for v in wa) or any(not math.isfinite(v) for v in wb):
            continue
        ma, mb = _mean(wa), _mean(wb)
        va = sum((v - ma) ** 2 for v in wa)
        vb = sum((v - mb) ** 2 for v in wb)
        if va == 0 or vb == 0:
            continue
        cov = sum((x - ma) * (y - mb) for x, y in zip(wa, wb))
        out[i] = cov / math.sqrt(va * vb)
    return out


def _factor_rank(x: list[float], n: int) -> list[float]:
    def rank_last(window: list[float]) -> float:
        last = window[-1]
        return sum(1 for v in window if v <= last) / len(window)

    return _rolling(x, n, rank_last)


def _zscore(x: list[float], n: int) -> list[float]:
    out = [math.nan] * len(x)
    for i in range(n - 1, len(x)):
        window = x[i - n + 1 : i + 1]
        if any(not math.isfinite(v) for v in window):
            continue
        sd = _std(window)
        if sd == 0:
            continue
        out[i] = (window[-1] - _mean(window)) / sd
    return out


def _roc(x: list[float], n: int) -> list[float]:
    out = [math.nan] * len(x)
    for i in range(n, len(x)):
        if x[i - n] != 0 and math.isfinite(x[i]) and math.isfinite(x[i - n]):
            out[i] = x[i] / x[i - n] - 1
    return out


def _delay(x: list[float], n: int) -> list[float]:
    return [math.nan] * n + x[:-n] if n else list(x)


def _atr(highs: list[float], lows: list[float], closes: list[float], n: int) -> list[float]:
    tr = [highs[0] - lows[0]]
    for i in range(1, len(highs)):
        tr.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )
    return _rolling(tr, n, _mean)


_FUNCS: dict[str, Any] = {
    "ma": lambda x, n: _rolling(x, n, _mean),
    "ema": lambda x, n: _ema(x, n),
    "std": lambda x, n: _rolling(x, n, _std),
    "zscore": lambda x, n: _zscore(x, n),
    "rank": lambda x, n: _factor_rank(x, n),
    "roc": lambda x, n: _roc(x, n),
    "rsi": lambda x, n: _rsi(x, n),
    "delay": lambda x, n: _delay(x, n),
    "abs": lambda x: [abs(v) for v in x],
    "log": lambda x: [_safe_unary(math.log)(v) if v > 0 else math.nan for v in x],
    "sqrt": lambda x: [_safe_unary(math.sqrt)(v) if v >= 0 else math.nan for v in x],
    "maximum": lambda a, b: _map2(a, b, max),
    "minimum": lambda a, b: _map2(a, b, min),
    "macd": lambda x, fast, slow: _map2(_ema(x, fast), _ema(x, slow), lambda a, b: a - b),
    "corr": lambda a, b, n: _corr(a, b, n),
    "atr": lambda h, l, c, n: _atr(h, l, c, n),
}


class _Evaluator:
    def __init__(self, env: dict[str, list[float]]) -> None:
        self.env = env
        self.length = len(env["close"])

    def run(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return self.run(node.body)
        if isinstance(node, ast.Constant):
            return _num(node.value)
        if isinstance(node, ast.Name):
            if node.id not in self.env:
                raise FactorError(f"未知字段: {node.id}")
            return self.env[node.id]
        if isinstance(node, ast.UnaryOp):
            value = self.run(node.operand)
            if isinstance(node.op, ast.USub):
                return _map2(value, [0.0] * self.length, lambda a, b: b - a)
            return value
        if isinstance(node, ast.BinOp):
            left = _series(self.run(node.left), self.length)
            right = _series(self.run(node.right), self.length)
            if isinstance(node.op, ast.Add):
                return _map2(left, right, lambda a, b: a + b)
            if isinstance(node.op, ast.Sub):
                return _map2(left, right, lambda a, b: a - b)
            if isinstance(node.op, ast.Mult):
                return _map2(left, right, lambda a, b: a * b)
            if isinstance(node.op, ast.Div):
                return _map2(left, right, _div)
            if isinstance(node.op, ast.Pow):
                return _map2(left, right, lambda a, b: math.pow(a, b))
            raise FactorError("不支持的运算")
        if isinstance(node, ast.Call):
            name = node.func.id  # type: ignore[union-attr]
            fn = _FUNCS[name]
            raw = [self.run(arg) for arg in node.args]
            if name == "atr":
                if len(raw) != 4:
                    raise FactorError("atr(high, low, close, n) 需要 4 个参数")
                h = _series(raw[0], self.length)
                l = _series(raw[1], self.length)
                c = _series(raw[2], self.length)
                return fn(h, l, c, _arg_int(raw[3]))
            if name in {"ma", "ema", "std", "zscore", "rank", "roc", "rsi", "delay", "corr"}:
                needed = 3 if name == "corr" else 2
                if len(raw) != needed:
                    raise FactorError(f"{name} 参数数量不正确")
                if name == "corr":
                    return fn(
                        _series(raw[0], self.length),
                        _series(raw[1], self.length),
                        _arg_int(raw[2]),
                    )
                return fn(_series(raw[0], self.length), _arg_int(raw[1]))
            if name in {"maximum", "minimum"}:
                if len(raw) != 2:
                    raise FactorError(f"{name} 需要 2 个参数")
                return fn(_series(raw[0], self.length), _series(raw[1], self.length))
            if name == "macd":
                if len(raw) != 3:
                    raise FactorError("macd(series, fast, slow) 需要 3 个参数")
                return fn(
                    _series(raw[0], self.length),
                    _arg_int(raw[1]),
                    _arg_int(raw[2]),
                )
            if len(raw) != 1:
                raise FactorError(f"{name} 需要 1 个参数")
            return fn(_series(raw[0], self.length))
        raise FactorError("不支持的表达式")


def _build_env(candles: list[dict[str, Any]]) -> dict[str, list[float]]:
    opens = [float(c["open"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    closes = [float(c["close"]) for c in candles]
    volumes = [float(c.get("vol") or 0.0) for c in candles]
    returns = [math.nan]
    for i in range(1, len(closes)):
        returns.append(closes[i] / closes[i - 1] - 1 if closes[i - 1] else math.nan)
    return {
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
        "hl2": [(h + l) / 2 for h, l in zip(highs, lows)],
        "hlc3": [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)],
        "ohlc4": [(o + h + l + c) / 4 for o, h, l, c in zip(opens, highs, lows, closes)],
        "returns": returns,
    }


def evaluate_series(formula: str, candles: list[dict[str, Any]]) -> list[float]:
    if not candles:
        raise FactorError("没有可用的 K 线数据")
    tree = _parse(formula)
    env = _build_env(candles)
    result = _Evaluator(env).run(tree)
    return _series(result, len(candles))


def latest_value(formula: str, candles: list[dict[str, Any]]) -> float | None:
    series = evaluate_series(formula, candles)
    for value in reversed(series):
        if math.isfinite(value):
            return float(value)
    return None


def preview_factor(
    formula: str, inst_id: str, bar: str = "4H", limit: int = 300
) -> dict[str, Any]:
    candles = live_data.candles(inst_id, bar, limit)
    if not candles:
        raise FactorError("引擎库里没有该标的的 K 线")
    series = evaluate_series(formula, candles)
    tail = series[-80:]
    points = [
        {"ts_ms": int(c["ts_ms"]), "value": (float(v) if math.isfinite(v) else None)}
        for c, v in zip(candles[-80:], tail)
    ]
    value = latest_value(formula, candles)
    return {"inst_id": inst_id, "bar": bar, "value": value, "points": points}


def evaluate_enabled(inst_id: str, bar: str = "4H", limit: int = 300) -> dict[str, Any]:
    candles = live_data.candles(inst_id, bar, limit)
    rows: list[dict[str, Any]] = []
    total = 0.0
    weight_sum = 0.0
    for factor in list_factors(enabled_only=True):
        item: dict[str, Any] = {
            "id": factor["id"],
            "name": factor["name"],
            "formula": factor["formula"],
            "weight": factor["weight"],
            "value": None,
            "error": None,
        }
        try:
            value = latest_value(factor["formula"], candles)
            item["value"] = value
            if value is not None and math.isfinite(value):
                total += value * factor["weight"]
                weight_sum += abs(factor["weight"])
        except FactorError as exc:
            item["error"] = str(exc)
        rows.append(item)
    return {
        "inst_id": inst_id,
        "bar": bar,
        "factors": rows,
        "composite": (total / weight_sum) if weight_sum else None,
    }

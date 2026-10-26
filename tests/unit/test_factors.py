"""因子表达式引擎的安全与计算边界测试。"""

import pytest

from dquant.api.factors import FactorError, evaluate_series


def _candles(count: int = 30) -> list[dict[str, float]]:
    return [
        {
            "open": float(100 + i),
            "high": float(101 + i),
            "low": float(99 + i),
            "close": float(100 + i),
            "vol": 1000.0,
        }
        for i in range(count)
    ]


def test_factor_engine_evaluates_whitelisted_rolling_formula() -> None:
    values = evaluate_series("zscore(close, 5) + roc(close, 3)", _candles())

    assert len(values) == 30
    assert values[-1] == pytest.approx(1.43802306, rel=1e-6)


def test_factor_engine_rejects_code_execution_syntax() -> None:
    with pytest.raises(FactorError, match="不支持的语法|不支持的函数调用"):
        evaluate_series("__import__('os').system('echo unsafe')", _candles())

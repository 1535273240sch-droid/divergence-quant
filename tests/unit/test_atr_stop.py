from decimal import Decimal

import pytest

from dquant.strategies.atr_stop import initial_stop, trailing_stop


def test_initial_stop_long_and_short() -> None:
    assert initial_stop("long", Decimal("100"), Decimal("2"), Decimal("2.5")) == Decimal("95.0")
    assert initial_stop("short", Decimal("100"), Decimal("2"), Decimal("2.5")) == Decimal("105.0")
    with pytest.raises(ValueError, match="positive"):
        initial_stop("long", Decimal("1"), Decimal("2"), Decimal("1"))


def test_trailing_stop_only_moves_in_favorable_direction() -> None:
    # 多头：参考高点上移，止损只能上移
    assert trailing_stop("long", Decimal("110"), Decimal("2"), Decimal("2"), None) == Decimal(
        "106"
    )
    assert (
        trailing_stop("long", Decimal("110"), Decimal("2"), Decimal("2"), Decimal("107"))
        == Decimal("107")
    )
    assert (
        trailing_stop("long", Decimal("108"), Decimal("2"), Decimal("2"), Decimal("107"))
        == Decimal("107")
    )
    # 空头：参考低点下移，止损只能下移
    assert trailing_stop("short", Decimal("90"), Decimal("2"), Decimal("2"), None) == Decimal(
        "94"
    )
    assert (
        trailing_stop("short", Decimal("90"), Decimal("2"), Decimal("2"), Decimal("93"))
        == Decimal("93")
    )
    assert (
        trailing_stop("short", Decimal("92"), Decimal("2"), Decimal("2"), Decimal("93"))
        == Decimal("93")
    )

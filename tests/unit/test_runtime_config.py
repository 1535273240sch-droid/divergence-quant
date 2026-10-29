from decimal import Decimal

import pytest

from dquant.runtime import StrategyRuntimeConfig
from dquant.strategies import create_strategy
from dquant.strategies.base import StrategyInputError


def test_runtime_config_round_trip_and_revision(tmp_path) -> None:
    path = tmp_path / "strategy_runtime.json"
    config = StrategyRuntimeConfig(path)
    assert config.params_for("MHD") == {}
    assert config.revision() == 0

    config.update("MHD", {"position_pct": 0.4, "leverage": 5})
    assert config.params_for("MHD")["position_pct"] == 0.4
    assert config.params_for("MHD")["leverage"] == 5
    assert config.revision() != 0
    snapshot = config.snapshot()
    assert snapshot["strategies"]["MHD"]["position_pct"] == 0.4
    assert path.stat().st_mode & 0o777 == 0o600


def test_mhd_strategy_validates_position_params() -> None:
    strategy = create_strategy("MHD", {"position_pct": 0.5, "position_multiplier": 1.0})
    assert strategy.params["position_pct"] == 0.5
    with pytest.raises(StrategyInputError, match="position_pct"):
        create_strategy("MHD", {"position_pct": Decimal("1.5")})
    with pytest.raises(StrategyInputError, match="leverage"):
        create_strategy("MHD", {"leverage": 0})

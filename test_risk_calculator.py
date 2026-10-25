import unittest

from risk_calculator import max_drawdown, position_size


class RiskCalculatorTests(unittest.TestCase):
    def test_max_drawdown_uses_largest_peak_to_trough_decline(self) -> None:
        equity_curve = [100.0, 120.0, 90.0, 110.0, 80.0]

        self.assertAlmostEqual(max_drawdown(equity_curve), 1 / 3)

    def test_max_drawdown_is_zero_for_monotonically_increasing_equity(self) -> None:
        self.assertEqual(max_drawdown([100.0, 110.0, 125.0]), 0.0)

    def test_position_size_applies_risk_and_all_sizing_factors(self) -> None:
        size = position_size(
            equity=1_000.0,
            risk_fraction=0.01,
            entry_price=100.0,
            stop_loss_price=95.0,
            contract_value=1.0,
            pool_factor=0.5,
            market_factor=0.5,
            weekend_factor=0.7,
        )

        self.assertAlmostEqual(size, 0.35)


if __name__ == "__main__":
    unittest.main()

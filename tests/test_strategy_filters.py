import unittest
from unittest.mock import patch

from strategy import evaluate_signals, is_favorable_market


def candle(close, open_price=None, high=None, low=None, vol=1000):
    open_price = close if open_price is None else open_price
    return {
        "open": open_price,
        "high": max(close, open_price) + 1 if high is None else high,
        "low": min(close, open_price) - 1 if low is None else low,
        "close": close,
        "vol": vol,
    }


def rising_market():
    return [candle(100 + i) for i in range(70)]


class StrategyFilterTests(unittest.TestCase):
    def test_market_regime_requires_price_above_rising_ma(self):
        self.assertTrue(is_favorable_market(rising_market()))
        falling = [candle(200 - i) for i in range(70)]
        self.assertFalse(is_favorable_market(falling))

    def test_rsi_waits_for_recovery_cross(self):
        closes = [130 - i for i in range(29)]
        candles = [candle(value, vol=1000) for value in closes]
        previous = closes[-1]
        candles.append(candle(previous + 6, open_price=previous + 1,
                              high=previous + 7, low=previous, vol=1800))
        signals = evaluate_signals({"반등주": candles}, rising_market())
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["strategy"], "RSI 과매도 반등")
        self.assertLessEqual(signals[0]["previous_rsi"], 28)
        self.assertGreater(signals[0]["rsi"], 30)

    def test_vbo_requires_volume_confirmation(self):
        candles = [candle(100 + i * 0.2, vol=1000) for i in range(29)]
        prev = candles[-1]
        target = 106.0 + (prev["high"] - prev["low"]) * 0.5
        candles.append(candle(target + 1, open_price=106.0,
                              high=target + 1.2, low=105.8, vol=1200))
        self.assertEqual(evaluate_signals({"돌파주": candles}, rising_market()), [])
        candles[-1]["vol"] = 1800
        signals = evaluate_signals({"돌파주": candles}, rising_market())
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["strategy"], "변동성 돌파 (추세)")
        self.assertEqual(signals[0]["entry_price"], candles[-1]["close"])

    def test_unfavorable_market_allows_half_size_rsi_recovery(self):
        candles = [candle(130 - i, vol=1000) for i in range(29)]
        candles.append(candle(108, open_price=103, high=109, low=102, vol=2000))
        falling = [candle(200 - i) for i in range(70)]
        signals = evaluate_signals({"후보": candles}, falling)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["strategy"], "RSI 과매도 반등")
        self.assertEqual(signals[0]["size_multiplier"], 0.5)

    def test_unfavorable_strong_vbo_is_disabled_by_default(self):
        candles = [candle(100 + i * 0.2, vol=1000) for i in range(29)]
        prev = candles[-1]
        target = 106.0 + (prev["high"] - prev["low"]) * 0.5
        candles.append(candle(target + 1, open_price=106.0,
                              high=target + 1.2, low=105.8, vol=2200))
        falling = [candle(200 - i) for i in range(70)]
        self.assertEqual(evaluate_signals({"강한돌파주": candles}, falling), [])

    def test_unfavorable_market_still_blocks_vbo(self):
        candles = [candle(100 + i * 0.2, vol=1000) for i in range(29)]
        prev = candles[-1]
        target = 106.0 + (prev["high"] - prev["low"]) * 0.5
        candles.append(candle(target + 1, open_price=106.0,
                              high=target + 1.2, low=105.8, vol=1800))
        falling = [candle(200 - i) for i in range(70)]
        self.assertEqual(evaluate_signals({"돌파주": candles}, falling), [])

    @patch("strategy.UNFAVORABLE_VBO_ENABLED", True)
    def test_unfavorable_market_allows_only_strong_vbo_at_fifteen_percent(self):
        candles = [candle(100 + i * 0.2, vol=1000) for i in range(29)]
        prev = candles[-1]
        target = 106.0 + (prev["high"] - prev["low"]) * 0.5
        candles.append(candle(target + 1, open_price=106.0,
                              high=target + 1.2, low=105.8, vol=2200))
        falling = [candle(200 - i) for i in range(70)]

        signals = evaluate_signals({"강한돌파주": candles}, falling)

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["strategy"], "비우호장 강한 VBO (15%)")
        self.assertAlmostEqual(signals[0]["size_multiplier"], 0.15 / 0.35)

    @patch("strategy.UNFAVORABLE_VBO_ENABLED", True)
    def test_unfavorable_vbo_blocks_late_breakout_chasing(self):
        candles = [candle(100 + i * 0.2, vol=1000) for i in range(29)]
        prev = candles[-1]
        target = 106.0 + (prev["high"] - prev["low"]) * 0.5
        candles.append(candle(target * 1.021, open_price=106.0,
                              high=target * 1.022, low=105.8, vol=2200))
        falling = [candle(200 - i) for i in range(70)]
        diagnostics = {}

        signals = evaluate_signals(
            {"늦은돌파주": candles}, falling, diagnostics=diagnostics
        )

        self.assertEqual(signals, [])
        self.assertIn("돌파가 추격", diagnostics["늦은돌파주"]["vbo_blocker"])

    @patch("strategy.UNFAVORABLE_VBO_ENABLED", True)
    def test_unfavorable_vbo_blocks_more_than_six_percent_day_gain(self):
        candles = [candle(100 + i * 0.2, vol=1000) for i in range(29)]
        prev = candles[-1]
        target = 106.0 + (prev["high"] - prev["low"]) * 0.5
        candles.append(candle(prev["close"] * 1.061, open_price=106.0,
                              high=prev["close"] * 1.062, low=105.8, vol=2200))
        falling = [candle(200 - i) for i in range(70)]
        diagnostics = {}

        signals = evaluate_signals(
            {"급등주": candles}, falling, diagnostics=diagnostics
        )

        self.assertEqual(signals, [])
        self.assertIn("당일 상승률", diagnostics["급등주"]["vbo_blocker"])

    def test_signal_diagnostics_explains_both_entry_paths(self):
        candles = [candle(100 + i, vol=1000) for i in range(30)]
        falling = [candle(200 - i) for i in range(70)]
        diagnostics = {}
        evaluate_signals({"진단주": candles}, falling, diagnostics=diagnostics)
        self.assertGreater(diagnostics["진단주"]["rsi"], 28)
        self.assertIn("직전 RSI", diagnostics["진단주"]["rsi_blocker"])
        self.assertEqual(diagnostics["진단주"]["vbo_blocker"], "시장 비우호")


if __name__ == "__main__":
    unittest.main()

import datetime
import os
import sqlite3
import tempfile
import unittest

from research_store import record_research_snapshot


class ResearchStoreTests(unittest.TestCase):
    def test_snapshot_is_local_queryable_and_deduplicated_per_slot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "research.db")
            context = {
                "market_regimes": {"KOSPI": False},
                "etf_rotation": {"regime": "inverse", "previous_close": 90,
                                 "ma": 100},
                "universe_date": "20261120", "universe_count": 1,
                "tradeability": [{"name": "후보", "code": "000001",
                                  "market": "KOSPI", "price": 10000,
                                  "eligible": True}],
                "signal_statuses": [{"name": "후보", "active": True,
                                     "strategy": "RSI", "count": 2}],
                "signal_diagnostics": [{"name": "후보", "previous_rsi": 27,
                                        "rsi": 30, "vbo_blocker": "거래량 부족"}],
                "entry_allowed": True,
            }
            position = {"code": "114800", "strategy": "ETF", "shares": 3,
                        "buy_price": 1000, "highest_price": 1050,
                        "stop_loss_pct": 0.025, "days_held": 1}
            trade = {"timestamp": "2026-11-20 09:01:00", "type": "BUY",
                     "code": "114800", "name": "인버스", "shares": 3,
                     "price": 1000, "strategy": "ETF"}
            now = datetime.datetime(2026, 11, 20, 10, 1, 20)
            record_research_snapshot(path, now, "running", "정상", True,
                                     context, {"인버스": position},
                                     {"114800": 1020}, [trade])
            record_research_snapshot(path, now.replace(minute=4), "running", "정상",
                                     True, context, {"인버스": position},
                                     {"114800": 1030}, [trade])
            with sqlite3.connect(path) as connection:
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM market_snapshots").fetchone()[0], 1)
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM candidate_snapshots").fetchone()[0], 1)
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM position_snapshots").fetchone()[0], 1)
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM trade_records").fetchone()[0], 1)
                self.assertAlmostEqual(connection.execute(
                    "SELECT current_price FROM position_snapshots").fetchone()[0], 1030)


if __name__ == "__main__":
    unittest.main()

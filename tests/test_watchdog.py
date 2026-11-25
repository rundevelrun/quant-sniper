import datetime
import unittest
from zoneinfo import ZoneInfo

from watchdog import should_restart


KST = ZoneInfo("Asia/Seoul")


class WatchdogTests(unittest.TestCase):
    def test_restarts_for_stale_weekday_market_heartbeat(self):
        now = datetime.datetime(2026, 11, 25, 10, 0, tzinfo=KST)
        restart, reason = should_restart(
            now, {"timestamp": "2026-11-25 09:56:59"}, 180
        )
        self.assertTrue(restart)
        self.assertIn("stale", reason)

    def test_keeps_recent_heartbeat(self):
        now = datetime.datetime(2026, 11, 25, 10, 0, tzinfo=KST)
        self.assertFalse(should_restart(
            now, {"timestamp": "2026-11-25 09:58:00"}, 180
        )[0])

    def test_never_restarts_outside_session_or_on_weekend(self):
        stale = {"timestamp": "2026-11-24 09:00:00"}
        self.assertFalse(should_restart(
            datetime.datetime(2026, 11, 25, 8, 59, tzinfo=KST), stale
        )[0])
        self.assertFalse(should_restart(
            datetime.datetime(2026, 11, 28, 10, 0, tzinfo=KST), stale
        )[0])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Restart quant-sniper when its market-hours heartbeat becomes stale."""

import datetime
import json
import os
import subprocess
from zoneinfo import ZoneInfo


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HEALTH_FILE = os.path.join(BASE_DIR, "health.json")
SERVICE_NAME = "quant_sniper.service"
KST = ZoneInfo("Asia/Seoul")
STALE_AFTER_SECONDS = 180


def should_restart(now, health, stale_after_seconds=STALE_AFTER_SECONDS):
    """Only flag a stale heartbeat during the weekday regular-session window."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    else:
        now = now.astimezone(KST)
    if now.weekday() >= 5:
        return False, "weekend"
    session_start = now.replace(hour=9, minute=0, second=0, microsecond=0)
    session_end = now.replace(hour=15, minute=32, second=0, microsecond=0)
    if not session_start <= now <= session_end:
        return False, "outside regular-session watchdog window"
    timestamp = str((health or {}).get("timestamp") or "").strip()
    if not timestamp:
        return True, "missing heartbeat timestamp"
    try:
        heartbeat = datetime.datetime.strptime(
            timestamp, "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=KST)
    except ValueError:
        return True, "invalid heartbeat timestamp"
    age = (now - heartbeat).total_seconds()
    if age > stale_after_seconds:
        return True, f"heartbeat stale for {int(age)}s"
    return False, f"heartbeat age {max(0, int(age))}s"


def load_health(path=HEALTH_FILE):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}


def main():
    restart, reason = should_restart(datetime.datetime.now(KST), load_health())
    if not restart:
        print(f"[WATCHDOG] healthy: {reason}")
        return 0
    print(f"[WATCHDOG] restarting {SERVICE_NAME}: {reason}")
    completed = subprocess.run(
        ["/usr/bin/systemctl", "restart", SERVICE_NAME], check=False
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())

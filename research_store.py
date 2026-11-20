"""Local-only append-only research data store for later strategy analysis."""

import datetime
import json
import sqlite3


SCHEMA = """
CREATE TABLE IF NOT EXISTS market_snapshots (
    slot TEXT PRIMARY KEY,
    captured_at TEXT NOT NULL,
    status TEXT,
    detail TEXT,
    market_open INTEGER NOT NULL,
    market_regimes_json TEXT NOT NULL,
    etf_regime TEXT,
    etf_previous_close REAL,
    etf_ma60 REAL,
    account_drawdown_pct REAL,
    daily_return_pct REAL,
    entry_allowed INTEGER NOT NULL,
    universe_date TEXT,
    universe_count INTEGER,
    raw_signal_count INTEGER,
    confirming_signal_count INTEGER,
    confirmed_signal_count INTEGER
);
CREATE TABLE IF NOT EXISTS candidate_snapshots (
    slot TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    market TEXT,
    sector TEXT,
    price REAL,
    eligible INTEGER,
    previous_rsi REAL,
    current_rsi REAL,
    rsi_blocker TEXT,
    vbo_blocker TEXT,
    signal_active INTEGER NOT NULL,
    signal_strategy TEXT,
    confirmation_count INTEGER,
    PRIMARY KEY (slot, code)
);
CREATE TABLE IF NOT EXISTS position_snapshots (
    slot TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    strategy TEXT,
    shares INTEGER,
    buy_price REAL,
    current_price REAL,
    return_pct REAL,
    highest_price REAL,
    stop_loss_pct REAL,
    trailing_stop_price REAL,
    trailing_active INTEGER NOT NULL,
    days_held INTEGER,
    PRIMARY KEY (slot, code)
);
CREATE TABLE IF NOT EXISTS trade_records (
    trade_key TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    type TEXT NOT NULL,
    code TEXT,
    name TEXT,
    strategy TEXT,
    shares INTEGER,
    entry_price REAL,
    exit_price REAL,
    net_return_pct REAL,
    reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_candidates_code_slot
    ON candidate_snapshots(code, slot);
CREATE INDEX IF NOT EXISTS idx_positions_code_slot
    ON position_snapshots(code, slot);
"""


def _five_minute_slot(now):
    minute = now.minute - (now.minute % 5)
    return now.replace(minute=minute, second=0, microsecond=0).isoformat()


def _trade_key(trade):
    return "|".join(str(trade.get(key, "")) for key in (
        "timestamp", "type", "code", "shares", "entry_price", "exit_price"
    ))


def record_research_snapshot(db_path, now, status, detail, market_open,
                             context, positions, quotes, trades):
    """Persist one five-minute state sample and synchronize immutable trades."""
    slot = _five_minute_slot(now)
    rotation = context.get("etf_rotation", {})
    signal_statuses = {
        item.get("name"): item for item in context.get("signal_statuses", [])
    }
    diagnostics = {
        item.get("name"): item for item in context.get("signal_diagnostics", [])
    }
    candidates = context.get("tradeability") or context.get("universe_symbols", [])

    with sqlite3.connect(db_path, timeout=5) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(SCHEMA)
        connection.execute(
            """INSERT OR REPLACE INTO market_snapshots VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (slot, now.isoformat(timespec="seconds"), status, detail,
             int(bool(market_open)), json.dumps(context.get("market_regimes", {}),
                                                ensure_ascii=False, sort_keys=True),
             rotation.get("regime"), rotation.get("previous_close"),
             rotation.get("ma"), context.get("account_drawdown_pct"),
             context.get("daily_return_pct"), int(bool(context.get("entry_allowed"))),
             context.get("universe_date"), context.get("universe_count", 0),
             context.get("raw_signal_count", 0),
             context.get("confirming_signal_count", 0),
             context.get("confirmed_signal_count", 0)),
        )
        for item in candidates:
            name = item.get("name", "")
            code = item.get("code", "")
            if not code:
                continue
            signal = signal_statuses.get(name, {})
            diagnostic = diagnostics.get(name, {})
            connection.execute(
                """INSERT OR REPLACE INTO candidate_snapshots VALUES
                   (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (slot, code, name, item.get("market"), item.get("sector"),
                 item.get("price"),
                 int(bool(item.get("eligible"))) if "eligible" in item else None,
                 diagnostic.get("previous_rsi"), diagnostic.get("rsi"),
                 diagnostic.get("rsi_blocker"), diagnostic.get("vbo_blocker"),
                 int(bool(signal.get("active"))), signal.get("strategy"),
                 signal.get("count", 0)),
            )
        for name, position in positions.items():
            code = position.get("code", "")
            if not code:
                continue
            current_price = quotes.get(code)
            buy_price = float(position.get("buy_price") or 0)
            return_pct = (
                (float(current_price) - buy_price) / buy_price * 100
                if current_price and buy_price else None
            )
            connection.execute(
                """INSERT OR REPLACE INTO position_snapshots VALUES
                   (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (slot, code, name, position.get("strategy"),
                 position.get("shares"), buy_price, current_price, return_pct,
                 position.get("highest_price"), position.get("stop_loss_pct"),
                 position.get("trailing_stop_price"),
                 int(bool(position.get("trailing_active"))),
                 position.get("days_held", 0)),
            )
        for trade in trades:
            connection.execute(
                """INSERT OR IGNORE INTO trade_records VALUES
                   (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (_trade_key(trade), trade.get("timestamp", ""),
                 trade.get("type", ""), trade.get("code"), trade.get("name"),
                 trade.get("strategy"), trade.get("shares"),
                 trade.get("entry_price") or trade.get("price"),
                 trade.get("exit_price"), trade.get("net_return_pct"),
                 trade.get("reason")),
            )

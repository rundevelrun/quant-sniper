import json
import os
import tempfile
import unittest
import datetime
from unittest.mock import patch

import config
from bot import (
    account_drawdown_controls, account_snapshot_baseline, build_tradeability,
    calculate_entry_shares,
    entry_cash_after_reserve,
    build_operational_status, confirm_persistent_signals, dashboard_status_label,
    is_dashboard_heartbeat_due,
    git_commit_and_push, is_entry_window,
    is_market_open, is_mim_entry_window, market_session_progress,
    mim_long_signal, save_json, trading_days_held, update_mim_state,
    etf_rotation_regime, etf_rotation_traded_today,
    estimate_external_cash_flow, sector_limit_reached, time_stop_reason,
    screening_preferred_max_price,
    update_dashboard_events, update_position_stop,
    render_dashboard,
)
from kiwoom_api import (
    KiwoomRestAPI, _market_name, _mixed_universe_candidates,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class SafetyTests(unittest.TestCase):
    def test_screener_mixes_rsi_watch_and_vbo_candidates(self):
        candidates = []
        for index in range(20):
            candidates.append((f"RSI{index}", f"1{index:05d}", {
                "previous_close": 10000,
                "previous_trading_value": 1000000 - index,
                "previous_rsi": 20 + index * 0.5,
                "vbo_trend_ready": False,
            }))
        for index in range(12):
            candidates.append((f"VBO{index}", f"2{index:05d}", {
                "previous_close": 10000,
                "previous_trading_value": 2000000 - index,
                "previous_rsi": 55,
                "vbo_trend_ready": True,
            }))

        selected = _mixed_universe_candidates(candidates, 20, 30000)

        self.assertEqual(len(selected), 20)
        self.assertEqual(
            sum(item[2]["screening_bucket"] == "RSI 반등 대기"
                for item in selected),
            12,
        )
        self.assertEqual(
            sum(item[2]["screening_bucket"] == "VBO 준비"
                for item in selected),
            8,
        )
        self.assertTrue(all(
            item[2]["vbo_trend_ready"]
            for item in selected if item[2]["screening_bucket"] == "VBO 준비"
        ))

    def test_screener_never_labels_rsi_above_threshold_as_rsi_watch(self):
        candidates = []
        for index, rsi in enumerate((24.0, 28.0, 28.1, 32.0, 45.0)):
            candidates.append((f"후보{index}", f"3{index:05d}", {
                "previous_close": 10000,
                "previous_trading_value": 1000000 - index,
                "previous_rsi": rsi,
                "vbo_trend_ready": index >= 2,
            }))

        selected = _mixed_universe_candidates(candidates, 5, 30000)
        rsi_selected = [
            item for item in selected
            if item[2]["screening_bucket"] == "RSI 반등 대기"
        ]

        self.assertEqual(len(rsi_selected), 2)
        self.assertTrue(all(
            item[2]["previous_rsi"] <= 28.0 for item in rsi_selected
        ))
        self.assertEqual(
            sum(item[2]["screening_bucket"] == "VBO 준비"
                for item in selected),
            3,
        )

    def test_thirty_symbol_universe_caps_rsi_bucket_at_fifteen(self):
        candidates = []
        for index in range(20):
            candidates.append((f"RSI{index}", f"4{index:05d}", {
                "previous_close": 10000,
                "previous_trading_value": 2000000 - index,
                "previous_rsi": 21.0,
                "vbo_trend_ready": False,
            }))
        for index in range(20):
            candidates.append((f"VBO{index}", f"5{index:05d}", {
                "previous_close": 10000,
                "previous_trading_value": 1000000 - index,
                "previous_rsi": 50.0,
                "vbo_trend_ready": True,
            }))

        selected = _mixed_universe_candidates(candidates, 30, 30000)

        self.assertEqual(len(selected), 30)
        self.assertEqual(
            sum(item[2]["screening_bucket"] == "RSI 반등 대기"
                for item in selected),
            15,
        )
        self.assertEqual(
            sum(item[2]["screening_bucket"] == "VBO 준비"
                for item in selected),
            15,
        )

    def test_dashboard_heartbeat_uses_fixed_half_hour_slots(self):
        before_first_slot = datetime.datetime(2026, 11, 19, 9, 0, 9)
        first_slot = datetime.datetime(2026, 11, 19, 9, 0, 10)
        after_first_push = datetime.datetime(2026, 11, 19, 9, 0, 11)
        second_slot = datetime.datetime(2026, 11, 19, 9, 30, 10)

        self.assertFalse(is_dashboard_heartbeat_due(
            datetime.datetime(2026, 11, 19, 8, 30, 10).timestamp(),
            before_first_slot,
        ))
        self.assertTrue(is_dashboard_heartbeat_due(
            datetime.datetime(2026, 11, 19, 8, 30, 10).timestamp(),
            first_slot,
        ))
        self.assertFalse(is_dashboard_heartbeat_due(
            after_first_push.timestamp(),
            datetime.datetime(2026, 11, 19, 9, 29, 59),
        ))
        self.assertTrue(is_dashboard_heartbeat_due(
            after_first_push.timestamp(), second_slot,
        ))

    def test_kiwoom_market_names_are_normalized(self):
        self.assertEqual(_market_name("거래소"), "KOSPI")
        self.assertEqual(_market_name("코스닥"), "KOSDAQ")
        self.assertEqual(_market_name("KOSDAQ"), "KOSDAQ")

    def test_dashboard_keeps_three_days_and_deduplicates_skips(self):
        import datetime
        now = datetime.datetime(2026, 11, 16, 10, 0)
        events = [{
            "timestamp": "2026-11-08T09:00:00", "type": "BUY", "code": "000001"
        }, {
            "timestamp": "2026-11-16T09:00:00", "type": "SKIP", "code": "000002",
            "reason": "위험 한도", "count": 1,
        }]
        updated = update_dashboard_events(events, {
            "type": "SKIP", "code": "000002", "reason": "위험 한도"
        }, now)
        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0]["count"], 2)

    def test_dashboard_renders_position_without_sensitive_account_data(self):
        class Feed:
            def quote(self, code):
                return {"price": 10300}
            def is_healthy(self):
                return True, ""
        markdown = render_dashboard(
            "running", "정규 감시 중",
            {"후보주": {"code": "000001", "shares": 2, "buy_price": 10000,
                      "highest_price": 10500, "stop_loss_pct": 0.03}},
            Feed(), [],
        )
        self.assertIn("후보주", markdown)
        self.assertIn("+3.00%", markdown)
        self.assertIn("## 현재 상태", markdown)
        self.assertIn("## 현재 보유 포지션", markdown)
        self.assertIn("## 최근 3일 중요 이벤트", markdown)
        self.assertIn("## 전체 성과", markdown)
        self.assertNotIn("계좌번호", markdown)
        self.assertNotIn("10,000", markdown)
        self.assertNotIn("| 2 |", markdown)
        self.assertIn("| 시스템 | 장 운영 |", markdown)
        self.assertIn("🟢 정상", markdown)

    def test_dashboard_status_uses_traffic_light_icons(self):
        self.assertEqual(dashboard_status_label("running"), "🟢 정상")
        self.assertEqual(dashboard_status_label("halted"), "🔴 거래 중단")
        self.assertEqual(dashboard_status_label("error"), "🔴 오류")
        self.assertEqual(dashboard_status_label("starting"), "🟡 starting")

    def test_dashboard_cumulative_performance_hides_krw_amounts(self):
        class Feed:
            def is_healthy(self):
                return True, ""

        markdown = render_dashboard(
            "running", "정규 감시 중", {}, Feed(), [],
            context={"all_trades": [{
                "type": "SELL", "shares": 2, "entry_price": 10000,
                "net_return_pct": 4.0, "net_profit_krw": 800,
                "cost_fee_krw": 40,
            }]},
        )
        performance = markdown.split("## 전체 성과", 1)[1]
        self.assertIn("거래가중 순수익률", performance)
        self.assertIn("+4.00%", performance)
        self.assertIn("0.20%", performance)
        self.assertNotIn("800원", performance)
        self.assertNotIn("40원", performance)

    def test_dashboard_lists_top_monitored_symbols_without_sensitive_prices(self):
        class Feed:
            def quote(self, code):
                return None
            def is_healthy(self):
                return True, ""
        markdown = render_dashboard(
            "running", "정규 감시 중", {}, Feed(), [],
            context={
                "market_favorable": True,
                "universe_symbols": [
                    {"name": "후보주", "code": "000001"},
                    {"name": "감시주", "code": "000002"},
                ],
                "signal_statuses": [
                    {"name": "후보주", "active": True,
                     "strategy": "RSI 과매도 반등", "count": 2},
                    {"name": "감시주", "active": False, "count": 0},
                ],
            },
        )
        self.assertIn("유력 감시 종목", markdown)
        self.assertIn("| 후보주 | 미분류 |", markdown)
        self.assertIn("| 감시주 | 미분류 |", markdown)
        self.assertNotIn("000001", markdown)
        self.assertIn("🟢 상승 국면", markdown)
        self.assertIn("🟢 RSI 과매도 반등 (2/3)", markdown)
        self.assertIn("🔴 신호 없음", markdown)

    def test_dashboard_hides_system_events_but_keeps_actionable_events(self):
        class Feed:
            def quote(self, code):
                return None
            def is_healthy(self):
                return True, ""
        events = [
            {"type": "SYSTEM", "reason": "봇 프로세스 시작",
             "timestamp": "2026-11-17T09:00:00", "count": 1},
            {"type": "RISK", "reason": "계좌 조회 실패",
             "timestamp": "2026-11-17T09:01:00", "count": 1},
        ]
        markdown = render_dashboard(
            "running", "정규 감시 중", {}, Feed(), events,
            now=__import__("datetime").datetime(2026, 11, 17, 10, 0),
        )
        self.assertNotIn("봇 프로세스 시작", markdown)
        self.assertIn("계좌 조회 실패", markdown)

    def test_position_size_obeys_allocation_and_trade_risk(self):
        # 30만원, 2만원 주식, 3% 손절: 35% 비중 한도는 5주, 위험 한도는 3주다.
        self.assertEqual(calculate_entry_shares(300000, 300000, 20000, 0.03), 3)
        # 7만원 1주는 35% 비중과 거래당 위험 0.7%를 모두 충족한다.
        self.assertEqual(calculate_entry_shares(300000, 300000, 70000, 0.03), 1)
        # 1주가 거래당 위험 한도를 넘으면 강제 1주 주문을 만들지 않는다.
        self.assertEqual(calculate_entry_shares(300000, 300000, 100000, 0.03), 0)
        # -8% 감속 구간에서는 신규 주문 수량이 절반 수준으로 줄어든다.
        self.assertEqual(calculate_entry_shares(300000, 300000, 20000, 0.03, 0.5), 1)

    def test_entry_cash_keeps_ten_percent_account_reserve(self):
        self.assertEqual(entry_cash_after_reserve(300000, 300000), 270000)
        self.assertAlmostEqual(entry_cash_after_reserve(300000, 50000), 20000)
        self.assertEqual(entry_cash_after_reserve(300000, 25000), 0)

    def test_sector_limit_allows_two_but_blocks_third_position(self):
        metadata = {
            "반도체A": {"sector": "반도체"},
            "반도체B": {"sector": "반도체"},
            "반도체C": {"sector": "반도체"},
        }
        one = {"반도체A": {"sector": "반도체"}}
        two = {**one, "반도체B": {"sector": "반도체"}}
        self.assertFalse(sector_limit_reached("반도체", one, metadata))
        self.assertTrue(sector_limit_reached("반도체", two, metadata))
        self.assertFalse(sector_limit_reached("미분류", two, metadata))

    def test_tradeability_separates_affordable_symbols(self):
        def candles(price):
            return [{"open": price, "high": price, "low": price,
                     "close": price, "vol": 1000} for _ in range(10)]
        result = build_tradeability(
            {"가능주": "000001", "고가주": "000002"},
            {"가능주": {"sector": "기계"}, "고가주": {"sector": "기계"}},
            {"가능주": candles(50000), "고가주": candles(100000)},
            300000, 300000,
        )
        self.assertTrue(result[0]["eligible"])
        self.assertEqual(result[0]["max_shares"], 1)
        self.assertFalse(result[1]["eligible"])
        self.assertEqual([item["name"] for item in result], ["가능주", "고가주"])

    def test_tradeability_applies_unfavorable_market_half_size(self):
        def candles(price):
            return [{"open": price, "high": price, "low": price,
                     "close": price, "vol": 1000} for _ in range(10)]
        result = build_tradeability(
            {"저가주": "000001", "중가주": "000002"},
            {
                "저가주": {"market": "KOSPI", "sector": "기계"},
                "중가주": {"market": "KOSPI", "sector": "화학"},
            },
            {"저가주": candles(20000), "중가주": candles(35000)},
            300000, 300000, 1.0, {"KOSPI": False},
        )
        self.assertEqual(result[0]["max_shares"], 1)
        self.assertEqual(result[0]["market_size_multiplier"], 0.5)
        self.assertEqual(result[0]["favorable_max_shares"], 2)
        self.assertEqual(result[0]["unfavorable_max_shares"], 1)
        self.assertEqual(result[1]["max_shares"], 0)
        self.assertIn("비우호장 50%", result[1]["reason"])

    def test_equity_drawdown_slows_then_halts_entries(self):
        peak, drawdown, multiplier, halted = account_drawdown_controls(920000, 1000000)
        self.assertEqual(peak, 1000000)
        self.assertAlmostEqual(drawdown, 0.08)
        self.assertEqual(multiplier, 0.5)
        self.assertFalse(halted)
        _, drawdown, multiplier, halted = account_drawdown_controls(880000, peak)
        self.assertAlmostEqual(drawdown, 0.12)
        self.assertEqual(multiplier, 0.5)
        self.assertTrue(halted)
        peak, drawdown, multiplier, halted = account_drawdown_controls(1100000, peak)
        self.assertEqual(peak, 1100000)
        self.assertEqual(drawdown, 0)
        self.assertEqual(multiplier, 1.0)
        self.assertFalse(halted)

    def test_external_cash_flow_excludes_market_pnl(self):
        previous = {
            "total_assets": 300000,
            "positions": {"000001": {"shares": 1, "current_price": 10000}},
        }
        market_move_only = {
            "total_assets": 302000,
            "positions": {"000001": {"shares": 1, "current_price": 12000}},
        }
        self.assertEqual(
            estimate_external_cash_flow(previous, market_move_only), 0
        )
        deposit = {
            "total_assets": 402000,
            "positions": {"000001": {"shares": 1, "current_price": 12000}},
        }
        self.assertEqual(
            estimate_external_cash_flow(previous, deposit), 100000
        )
        self.assertEqual(
            account_snapshot_baseline(deposit)["total_assets"], 402000
        )

    def test_external_cash_flow_skips_changed_positions(self):
        previous = {"total_assets": 300000, "positions": {}}
        after_buy = {
            "total_assets": 300000,
            "positions": {"000001": {"shares": 1, "current_price": 50000}},
        }
        self.assertEqual(estimate_external_cash_flow(previous, after_buy), 0)

    def test_profitable_position_gets_extended_time_stop(self):
        losing = {"buy_price": 10000, "days_held": 7, "trailing_active": False}
        self.assertIsNotNone(time_stop_reason(losing, 9900))
        profitable = {"buy_price": 10000, "days_held": 7, "trailing_active": False}
        self.assertIsNone(time_stop_reason(profitable, 10100))
        profitable["days_held"] = 14
        self.assertIsNotNone(time_stop_reason(profitable, 10100))
        profitable["trailing_active"] = True
        self.assertIsNone(time_stop_reason(profitable, 10100))

    def test_operational_status_preserves_risk_reason(self):
        status, detail = build_operational_status(
            True, ["계좌 조회 실패", "계좌 조회 실패", "체결 미확인"], 1
        )
        self.assertEqual(status, "halted")
        self.assertEqual(detail, "계좌 조회 실패; 체결 미확인")

    def test_signal_must_persist_three_loops(self):
        signal = {"name": "후보주", "strategy": "변동성 돌파 (추세)"}
        counts = {}
        for expected in (0, 0, 1):
            confirmed, counts = confirm_persistent_signals([signal], counts, 3)
            self.assertEqual(len(confirmed), expected)
        confirmed, counts = confirm_persistent_signals([], counts, 3)
        self.assertEqual(confirmed, [])
        self.assertEqual(counts, {})

    @patch("bot.subprocess.run")
    def test_trade_push_failure_is_reported(self, run):
        import subprocess
        run.side_effect = subprocess.CalledProcessError(1, ["git", "push"])
        self.assertFalse(git_commit_and_push("test"))

    def test_entry_window_avoids_open_and_late_session(self):
        import datetime
        self.assertFalse(is_entry_window(datetime.datetime(2026, 11, 13, 9, 14)))
        self.assertTrue(is_entry_window(datetime.datetime(2026, 11, 13, 9, 15)))
        self.assertTrue(is_entry_window(datetime.datetime(2026, 11, 13, 14, 30)))
        self.assertFalse(is_entry_window(datetime.datetime(2026, 11, 13, 14, 31)))
        self.assertEqual(market_session_progress(datetime.datetime(2026, 11, 13, 9, 0)), 0)
        self.assertEqual(market_session_progress(datetime.datetime(2026, 11, 13, 15, 30)), 1)

    def test_mim_collects_only_boundary_prices_and_requires_three_positive_periods(self):
        state = {}
        quote = {"open": 101, "price": 102}
        state = update_mim_state(
            state, datetime.datetime(2026, 11, 20, 9, 30, 10), quote, 100
        )
        quote["price"] = 103
        state = update_mim_state(
            state, datetime.datetime(2026, 11, 20, 10, 0, 10), quote, 100
        )
        quote["price"] = 104
        state = update_mim_state(
            state, datetime.datetime(2026, 11, 20, 14, 30, 10), quote, 100
        )
        quote["price"] = 105
        state = update_mim_state(
            state, datetime.datetime(2026, 11, 20, 15, 0, 10), quote, 100
        )
        self.assertTrue(mim_long_signal(state))

    def test_mim_late_restart_does_not_reconstruct_missed_boundaries(self):
        state = update_mim_state(
            {}, datetime.datetime(2026, 11, 20, 12, 0),
            {"open": 101, "price": 105}, 100,
        )
        self.assertFalse(mim_long_signal(state))
        self.assertNotIn("0930", state["snapshots"])
        self.assertTrue(is_mim_entry_window(
            datetime.datetime(2026, 11, 20, 15, 0)
        ))
        self.assertFalse(is_mim_entry_window(
            datetime.datetime(2026, 11, 20, 15, 6)
        ))

    def test_etf_rotation_uses_only_completed_daily_trend(self):
        start = datetime.date(2026, 8, 1)
        rising = [
            {"date": (start + datetime.timedelta(days=index)).strftime("%Y%m%d"),
             "close": 100 + index}
            for index in range(65)
        ]
        falling = [
            {"date": (start + datetime.timedelta(days=index)).strftime("%Y%m%d"),
             "close": 200 - index}
            for index in range(65)
        ]
        flat = [dict(item, close=100) for item in rising]
        now = datetime.datetime(2026, 11, 20, 10, 0)
        self.assertEqual(etf_rotation_regime(rising, now)[0], "long")
        self.assertEqual(etf_rotation_regime(falling, now)[0], "inverse")
        self.assertEqual(etf_rotation_regime(flat, now)[0], "cash")
        rising.append({"date": "20261120", "close": 1})
        self.assertEqual(etf_rotation_regime(rising, now)[0], "long")

    def test_etf_rotation_does_not_reenter_on_same_day(self):
        trades = [{
            "timestamp": "2026-11-20 09:20:00", "type": "SELL",
            "strategy": "시장 ETF 3상태 추세전환",
        }]
        self.assertTrue(etf_rotation_traded_today(trades, "2026-11-20"))
        self.assertFalse(etf_rotation_traded_today(trades, "2026-11-21"))

    def test_dashboard_shows_separate_mim_pilot_state(self):
        class Feed:
            def quote(self, code):
                return None
            def is_healthy(self):
                return True, ""
        with patch("bot.MIM_ENABLED", True):
            markdown = render_dashboard(
                "running", "정규 감시 중", {}, Feed(), [],
                context={"mim_state": {
                    "ready": True, "long_signal": True,
                    "entry_attempted": False,
                }},
            )
        self.assertIn("| 장마감 모멘텀 | 진입 신호 | KODEX 200 |", markdown)

    def test_market_open_requires_krx_trading_day(self):
        import datetime
        self.assertTrue(is_market_open(datetime.datetime(2026, 11, 13, 10, 0), True))
        self.assertFalse(is_market_open(datetime.datetime(2026, 11, 13, 10, 0), False))
        self.assertFalse(is_market_open(datetime.datetime(2026, 11, 14, 10, 0), True))
        self.assertFalse(is_market_open(datetime.datetime(2026, 11, 13, 8, 59), True))

    def test_live_mode_requires_explicit_enable(self):
        with patch.object(config, "LIVE_TRADING_ENABLED", False):
            with self.assertRaises(RuntimeError):
                KiwoomRestAPI("key", "secret", "account")

    def test_live_mode_requires_account_number(self):
        with patch.object(config, "LIVE_TRADING_ENABLED", True):
            with self.assertRaises(RuntimeError):
                KiwoomRestAPI("key", "secret", "")

    def test_atomic_json_save(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "state.json")
            save_json(path, {"ok": True})
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle), {"ok": True})
            self.assertFalse(os.path.exists(path + ".tmp"))

    def test_holding_days_increment_once_per_date(self):
        pos = {"days_held": 1, "last_held_date": "2026-11-12"}
        self.assertEqual(trading_days_held(pos, "2026-11-13"), 2)
        self.assertEqual(trading_days_held(pos, "2026-11-13"), 2)

    @patch("urllib.request.urlopen")
    def test_account_snapshot_parses_broker_positions(self, urlopen):
        urlopen.return_value = FakeResponse({
            "return_code": 0,
            "prsm_dpst_aset_amt": "000000001000000",
            "tot_evlt_pl": "-00000000001000",
            "acnt_evlt_remn_indv_tot": [{
                "stk_cd": "A005930", "stk_nm": "삼성전자",
                "rmnd_qty": "000000000000003", "trde_able_qty": "3",
                "pur_pric": "000000000070000", "cur_prc": "71000",
                "evltv_prft": "3000",
            }],
        })
        with patch.object(config, "LIVE_TRADING_ENABLED", True):
            api = KiwoomRestAPI("key", "secret", "account")
        api.get_token = lambda: "token"
        api.get_cash_balance = lambda: 500000
        snapshot = api.get_account_snapshot()
        self.assertTrue(snapshot["ok"])
        self.assertEqual(snapshot["positions"]["005930"]["shares"], 3)
        self.assertEqual(snapshot["positions"]["005930"]["buy_price"], 70000)

    @patch("urllib.request.urlopen")
    def test_cash_balance_returns_numeric_orderable_amount(self, urlopen):
        urlopen.return_value = FakeResponse({"ord_alow_amt": "00000000105000"})
        with patch.object(config, "LIVE_TRADING_ENABLED", True):
            api = KiwoomRestAPI("key", "secret", "account")
        api.get_token = lambda: "token"
        balance = api.get_cash_balance()
        self.assertEqual(balance, 105000.0)
        self.assertIsInstance(balance, float)

    @patch("urllib.request.urlopen")
    def test_cash_balance_uses_cache_until_forced(self, urlopen):
        urlopen.side_effect = [
            FakeResponse({"ord_alow_amt": "100000"}),
            FakeResponse({"ord_alow_amt": "120000"}),
        ]
        with patch.object(config, "LIVE_TRADING_ENABLED", True):
            api = KiwoomRestAPI("key", "secret", "account")
        api.get_token = lambda: "token"
        self.assertEqual(api.get_cash_balance(), 100000.0)
        self.assertEqual(api.get_cash_balance(), 100000.0)
        self.assertEqual(api.get_cash_balance(force_refresh=True), 120000.0)
        self.assertEqual(urlopen.call_count, 2)

    @patch("urllib.request.urlopen")
    def test_etf_order_can_be_forced_to_krx(self, urlopen):
        urlopen.return_value = FakeResponse({"return_code": 0, "ord_no": "123"})
        with patch.object(config, "LIVE_TRADING_ENABLED", True):
            api = KiwoomRestAPI("key", "secret", "account")
        api.get_token = lambda: "token"
        result = api.send_buy_order(
            "114800", "KODEX 인버스 추세", 1, exchange="KRX"
        )
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(payload["dmst_stex_tp"], "KRX")

    @patch("urllib.request.urlopen")
    def test_execution_uses_weighted_actual_fill_price(self, urlopen):
        urlopen.return_value = FakeResponse({
            "return_code": 0,
            "acnt_ord_cntr_prst_array": [
                {"ord_no": "0000123", "cntr_qty": "2", "cntr_uv": "10000"},
                {"ord_no": "0000123", "cntr_qty": "1", "cntr_uv": "10300"},
            ],
        })
        with patch.object(config, "LIVE_TRADING_ENABLED", True):
            api = KiwoomRestAPI("key", "secret", "account")
        api.get_token = lambda: "token"
        result = api.get_order_execution("123", "005930")
        self.assertEqual(result["filled_qty"], 3)
        self.assertEqual(result["fill_price"], 10100)

    def test_kiwoom_previous_day_universe_uses_previous_market_cap(self):
        with patch.object(config, "LIVE_TRADING_ENABLED", True):
            api = KiwoomRestAPI("key", "secret", "account")
        api.get_token = lambda: "token"
        api._post_domestic = unittest.mock.Mock(side_effect=[
            {"return_code": 0, "pred_trde_qty_upper": [
                {"stk_cd": "000001", "stk_nm": "후보주"},
                {"stk_cd": "069500", "stk_nm": "KODEX200"},
            ]},
            {"return_code": 0, "lastPrice": "20000", "listCount": "100000000",
             "marketName": "KOSPI", "upName": "반도체", "auditInfo": "",
             "orderWarning": "0"},
            {"return_code": 0, "stk_dt_pole_chart_qry": [
                {"dt": "20260101", "trde_prica": "500000"},
            ]},
            {"return_code": 0, "lastPrice": "30000", "listCount": "100000000",
             "marketName": "ETF", "auditInfo": "", "orderWarning": "0"},
        ])
        universe = api.get_previous_day_universe(
            20, 1_000_000_000_000, 5_000_000_000_000
        )
        self.assertEqual(universe, {"후보주": "000001"})
        self.assertEqual(api.last_universe_metadata["후보주"]["sector"], "반도체")
        self.assertEqual(api.last_universe_metadata["후보주"]["market"], "KOSPI")

    def test_screener_excludes_candidate_account_cannot_afford(self):
        with patch.object(config, "LIVE_TRADING_ENABLED", True):
            api = KiwoomRestAPI("key", "secret", "account")
        api._post_domestic = unittest.mock.Mock(side_effect=[
            {"return_code": 0, "pred_trde_qty_upper": [
                {"stk_cd": "000001", "stk_nm": "고가주"},
                {"stk_cd": "000002", "stk_nm": "매수가능주"},
            ]},
            {"return_code": 0, "lastPrice": "80000", "listCount": "10000000",
             "marketName": "KOSPI", "upName": "기계", "auditInfo": "",
             "orderWarning": "0"},
            {"return_code": 0, "stk_dt_pole_chart_qry": [
                {"dt": "20260101", "trde_prica": "900000"},
            ]},
            {"return_code": 0, "lastPrice": "20000", "listCount": "30000000",
             "marketName": "KOSDAQ", "upName": "화학", "auditInfo": "",
             "orderWarning": "0"},
            {"return_code": 0, "stk_dt_pole_chart_qry": [
                {"dt": "20260101", "trde_prica": "500000"},
            ]},
        ])
        universe = api.get_previous_day_universe(
            2, 500_000_000_000, 5_000_000_000_000, 30000
        )
        self.assertEqual(list(universe), ["매수가능주"])
        self.assertNotIn("고가주", api.last_universe_metadata)

    def test_mixed_universe_does_not_fill_with_unaffordable_symbols(self):
        candidates = [
            ("가능주", "000001", {
                "previous_close": 20000,
                "previous_trading_value": 1000000,
                "previous_rsi": 25.0,
                "vbo_trend_ready": False,
            }),
            ("고가RSI", "000002", {
                "previous_close": 80000,
                "previous_trading_value": 3000000,
                "previous_rsi": 20.0,
                "vbo_trend_ready": False,
            }),
            ("고가VBO", "000003", {
                "previous_close": 90000,
                "previous_trading_value": 4000000,
                "previous_rsi": 50.0,
                "vbo_trend_ready": True,
            }),
        ]

        selected = _mixed_universe_candidates(candidates, 30, 30000)

        self.assertEqual([item[0] for item in selected], ["가능주"])

    def test_screening_price_cap_uses_unfavorable_risk_budget(self):
        cap = screening_preferred_max_price({
            "ok": True, "total_assets": 306170, "cash": 306170,
        })
        self.assertAlmostEqual(cap, 24354.431818181816)

    def test_kiwoom_daily_candles_are_returned_oldest_first(self):
        with patch.object(config, "LIVE_TRADING_ENABLED", True):
            api = KiwoomRestAPI("key", "secret", "account")
        api._post_domestic = unittest.mock.Mock(return_value={
            "return_code": 0,
            "stk_dt_pole_chart_qry": [
                {"dt": "20261113", "open_pric": "100", "high_pric": "110",
                 "low_pric": "90", "cur_prc": "105", "trde_qty": "200"},
                {"dt": "20261112", "open_pric": "95", "high_pric": "101",
                 "low_pric": "94", "cur_prc": "100", "trde_qty": "150"},
            ],
        })
        candles = api.get_daily_candles("000001", 2)
        self.assertEqual([c["date"] for c in candles], ["20261112", "20261113"])
        self.assertEqual(candles[-1]["close"], 105)

    def test_high_water_stop_ratchets_and_never_moves_down(self):
        pos = {"buy_price": 10000, "stop_loss_pct": 0.03,
               "trailing_gap_pct": 0.02, "highest_price": 10000}
        stop, reason = update_position_stop(pos, 10400)
        self.assertEqual(stop, 10100)
        self.assertEqual(reason, "수익 보호 스탑")
        stop, reason = update_position_stop(pos, 11000)
        self.assertEqual(stop, 10780)
        self.assertTrue(pos["trailing_active"])
        stop_after_pullback, _ = update_position_stop(pos, 10800)
        self.assertEqual(stop_after_pullback, stop)


if __name__ == "__main__":
    unittest.main()

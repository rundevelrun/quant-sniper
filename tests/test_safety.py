import json
import os
import tempfile
import unittest
from unittest import mock
import datetime
from unittest.mock import patch

import config
from bot import (
    account_drawdown_controls, account_snapshot_baseline, build_tradeability,
    build_etf_tradeability,
    calculate_entry_shares,
    entry_cash_after_reserve,
    build_operational_status, confirm_persistent_signals, dashboard_status_label,
    is_dashboard_five_minute_due, is_dashboard_heartbeat_due,
    git_commit_and_push, is_entry_window, is_stock_entry_window,
    stock_entry_block_reason,
    apply_core_fill, core_code_conflicts, core_dashboard_info,
    remember_core_last_prices, mark_flow_rebalance,
    restore_dashboard_context, resolve_external_flow, rotation_alloc_pct, plan_rotation_resize, apply_rotation_resize_fill, mark_flow_rebalance, holdings_changed, position_weight_pcts, dashboard_weight_cell,
    dashboard_holding_count_label, dashboard_long_term_rows,
    core_desired_shares, core_rebalance_due, core_shares,
    is_core_rebalance_window, plan_core_rebalance,
    account_with_parking_cash, apply_parking_fill, is_parking_window,
    parking_buy_shares, parking_dashboard_info, parking_liquidation_value,
    parking_reference_price, parking_sell_shares_needed,
    is_market_open, is_mim_entry_window, market_session_progress,
    mim_long_signal, save_json, trading_days_held, update_mim_state,
    etf_rotation_regime, etf_rotation_traded_today,
    etf_entry_gap_status,
    multi_asset_trend_selection, sector_etf_trend_selection,
    sector_etf_traded_today,
    estimate_external_cash_flow, sector_limit_reached, time_stop_reason,
    apply_intraday_surge_limits, intraday_surge_max_price,
    screening_preferred_max_price,
    get_kiwoom_candles,
    restore_daily_account_metrics, restore_position_last_prices,
    update_dashboard_events, update_position_stop,
    render_dashboard, return_pct_svg,
    build_dashboard_state, update_cumulative_account_metrics,
    preserve_cumulative_risk_fields,
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
    def test_return_svg_uses_red_for_profit_and_blue_for_loss(self):
        profit = return_pct_svg(1.25)
        loss = return_pct_svg(-0.75)
        self.assertIn("#cf222e", profit)
        self.assertIn("+1.25%", profit)
        self.assertIn("#0969da", loss)
        self.assertIn("-0.75%", loss)
        self.assertIn('font-size="16"', profit)
        self.assertIn('font-weight="700"', profit)
        self.assertIn('y="14"', profit)
        self.assertIn('width="44"', profit)
        self.assertIn('textLength="40"', profit)

    def test_dashboard_return_svg_url_changes_with_value_to_avoid_cache(self):
        from bot import dashboard_return
        context = {"svg_returns": True}
        first = dashboard_return(1.25, "position-1", context)
        second = dashboard_return(1.50, "position-1", context)
        self.assertIn("position-1-v5-p1_25.svg", first)
        self.assertIn("position-1-v5-p1_50.svg", second)

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

    def test_dashboard_position_refresh_uses_fixed_five_minute_slots(self):
        previous_push = datetime.datetime(2026, 11, 19, 9, 0, 11).timestamp()
        self.assertFalse(is_dashboard_five_minute_due(
            previous_push, datetime.datetime(2026, 11, 19, 9, 5, 9)
        ))
        self.assertTrue(is_dashboard_five_minute_due(
            previous_push, datetime.datetime(2026, 11, 19, 9, 5, 10)
        ))
        just_pushed = datetime.datetime(2026, 11, 19, 9, 7, 0).timestamp()
        self.assertFalse(is_dashboard_five_minute_due(
            just_pushed, datetime.datetime(2026, 11, 19, 9, 10, 9)
        ))
        self.assertTrue(is_dashboard_five_minute_due(
            just_pushed, datetime.datetime(2026, 11, 19, 9, 10, 10)
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
            def quote(self, code, *_):
                return {"price": 10300}
            def is_healthy(self):
                return True, ""
        markdown = render_dashboard(
            "running", "정규 감시 중",
            {"후보주": {"code": "000001", "shares": 2, "buy_price": 10000,
                      "highest_price": 10500, "stop_loss_pct": 0.03}},
            Feed(), [], context={"svg_returns": True},
        )
        self.assertIn("후보주", markdown)
        self.assertIn("+3.00%", markdown)
        self.assertIn('<table width="100%">', markdown)
        self.assertIn('width="44" height="22"', markdown)
        self.assertIn('<th width="76" nowrap align="center">수익률</th>', markdown)
        self.assertIn('<th width="76" nowrap align="center">오늘 수익률</th>', markdown)
        self.assertIn('<td width="76" nowrap align="center" valign="middle">', markdown)
        self.assertNotIn('width="70"', markdown)
        self.assertIn("## 현재 상태", markdown)
        self.assertIn("보유 중 매 5분 10초", markdown)
        self.assertIn("00·05·10…분", markdown)
        self.assertIn("## 현재 보유 포지션", markdown)
        self.assertNotIn("## 최근 3일 중요 이벤트", markdown)
        self.assertIn("## 청산 전체 기록", markdown)
        self.assertIn("## 전체 성과", markdown)
        self.assertNotIn("계좌번호", markdown)
        self.assertNotIn("10,000", markdown)
        self.assertNotIn("| 2 |", markdown)
        self.assertIn("| 시스템 | 장 운영 |", markdown)
        self.assertIn("🟢 정상", markdown)
        self.assertLess(markdown.index("## 전략별 현재 판단"),
                        markdown.index("## 현재 보유 포지션"))
        self.assertLess(markdown.index("## 현재 보유 포지션"),
                        markdown.index("## 계좌 안전장치"))
        self.assertNotIn("<th width=\"14%\">거래당 위험</th>", markdown)
        self.assertNotIn("종목당 한도</th>", markdown)
        self.assertLess(markdown.index("## 현재 보유 포지션"),
                        markdown.index("## 전체 성과"))

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

    def test_dashboard_lists_all_closed_trades_without_amounts(self):
        class Feed:
            def is_healthy(self):
                return True, ""
        trades = [{
            "timestamp": f"2026-11-{day:02d} 14:00:00", "type": "SELL",
            "name": f"종목{day}", "code": f"{day:06d}",
            "strategy": "RSI 반등", "entry_date": "2026-11-01",
            "days_held": 3, "net_return_pct": 1.25,
            "reason": "고점 추적", "shares": 99,
            "entry_price": 10000, "exit_price": 10125,
            "net_profit_krw": 12345,
        } for day in range(1, 12)]
        markdown = render_dashboard(
            "running", "정규장 운영 시간 외", {}, Feed(), [],
            context={"all_trades": trades},
        )
        closed = markdown.split("## 청산 전체 기록", 1)[1].split("## ", 1)[0]
        self.assertIn("<th>진입일</th><th>청산일</th>", closed)
        self.assertIn('<th width="76" nowrap align="center">순수익률</th>', closed)
        self.assertIn("종목11", closed)
        self.assertIn(">종목1</td>", closed)
        self.assertNotIn("12,345", closed)
        self.assertNotIn("10,000", closed)
        self.assertNotIn("99주", closed)

    def test_dashboard_uses_saved_last_price_without_realtime_quote(self):
        class Feed:
            def quote(self, code, *_):
                return None
            def is_healthy(self):
                return True, ""
        markdown = render_dashboard(
            "running", "정규장 운영 시간 외",
            {"보유주": {"code": "000001", "shares": 1,
                      "buy_price": 10000, "last_price": 10100,
                      "days_held": 1, "strategy": "RSI 과매도 반등"}},
            Feed(), [],
        )
        self.assertIn("+1.00%", markdown)

    def test_missing_last_price_is_restored_from_latest_daily_close(self):
        class Api:
            def get_daily_candles(self, code, count):
                return [{"date": "20261120", "close": 10100}]
        positions = {"보유주": {"code": "000001", "buy_price": 10000}}
        self.assertTrue(restore_position_last_prices(
            Api(), positions, "2026-11-20"
        ))
        self.assertEqual(positions["보유주"]["last_price"], 10100)

    @mock.patch('bot.STOCK_STRATEGIES_ENABLED', True)
    def test_dashboard_lists_all_monitored_symbols_without_sensitive_prices(self):
        class Feed:
            def quote(self, code, *_):
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
        self.assertIn("## 감시 종목 전체", markdown)
        self.assertIn("| 후보주 | 미분류 |", markdown)
        self.assertIn("| 감시주 | 미분류 |", markdown)
        self.assertNotIn("000001", markdown)
        self.assertIn("🟢 상승 국면", markdown)
        self.assertIn("🟢 RSI 과매도 반등 (2/3)", markdown)
        self.assertIn("🔴 신호 없음", markdown)
        self.assertIn("</table>\n\n## 감시 종목 전체", markdown)

    @mock.patch('bot.STOCK_STRATEGIES_ENABLED', True)
    def test_dashboard_does_not_limit_monitored_symbols_to_ten(self):
        class Feed:
            def quote(self, code, *_):
                return None
            def is_healthy(self):
                return True, ""
        symbols = [
            {"name": f"감시주{index}", "code": f"{index:06d}"}
            for index in range(12)
        ]
        markdown = render_dashboard(
            "running", "정규 감시 중", {}, Feed(), [],
            context={"universe_count": 12, "universe_symbols": symbols},
        )
        for index in range(12):
            self.assertIn(f"| 감시주{index} |", markdown)

    def test_dashboard_hides_system_events_but_keeps_actionable_events(self):
        class Feed:
            def quote(self, code, *_):
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
        # 이벤트 표는 제거됨(리스크는 현재 상태 줄과 알림으로 확인).
        self.assertNotIn("## 최근 3일 중요 이벤트", markdown)

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
            {"가능주": candles(30000), "고가주": candles(100000)},
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
            {"저가주": candles(15000), "중가주": candles(35000)},
            300000, 300000, 1.0, {"KOSPI": False},
        )
        self.assertEqual(result[0]["max_shares"], 1)
        self.assertEqual(result[0]["market_size_multiplier"], 0.5)
        self.assertEqual(result[0]["favorable_max_shares"], 2)
        self.assertEqual(result[0]["unfavorable_max_shares"], 1)
        self.assertEqual(result[1]["max_shares"], 0)
        self.assertIn("비우호장 50%", result[1]["reason"])

    def test_sector_etf_allows_one_share_when_risk_and_cash_are_safe(self):
        result = build_etf_tradeability(
            {"091160": "KODEX 반도체"}, "섹터 ETF", 0.15, 0.025,
            320000, 320000, {"091160": {"price": 84600}},
            allow_one_share_over_allocation=True,
        )
        self.assertTrue(result[0]["eligible"])
        self.assertEqual(result[0]["max_shares"], 1)
        self.assertTrue(result[0]["one_share_exception"])

    def test_sector_etf_one_share_exception_never_breaks_risk_limit(self):
        result = build_etf_tradeability(
            {"091160": "KODEX 반도체"}, "섹터 ETF", 0.15, 0.04,
            320000, 320000, {"091160": {"price": 84600}},
            allow_one_share_over_allocation=True,
        )
        self.assertFalse(result[0]["eligible"])
        self.assertEqual(result[0]["max_shares"], 0)

    def test_etf_tradeability_matches_allocation_risk_and_cash_limits(self):
        result = build_etf_tradeability(
            {"069500": "KODEX 200"}, "다중자산 ETF", 0.30, 0.025,
            300000, 300000, {"069500": {"price": 76210}},
        )
        self.assertTrue(result[0]["eligible"])
        self.assertEqual(result[0]["max_shares"], 1)
        self.assertEqual(result[0]["allocation_pct"], 30.0)

    def test_equity_drawdown_slows_then_halts_entries(self):
        peak, drawdown, multiplier, halted = account_drawdown_controls(920000, 1000000)
        self.assertEqual(peak, 1000000)
        self.assertAlmostEqual(drawdown, 0.08)
        self.assertEqual(multiplier, 1.0)
        self.assertFalse(halted)
        _, drawdown, multiplier, halted = account_drawdown_controls(880000, peak)
        self.assertAlmostEqual(drawdown, 0.12)
        self.assertEqual(multiplier, 0.5)
        self.assertFalse(halted)
        _, drawdown, multiplier, halted = account_drawdown_controls(800000, peak)
        self.assertAlmostEqual(drawdown, 0.20)
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

    def test_daily_return_is_restored_after_market_close_restart(self):
        class Api:
            def get_account_snapshot(self):
                return {"ok": True, "total_assets": 303000,
                        "positions": {"000001": {
                            "shares": 1, "current_price": 13000,
                        }}}
        risk_state = {
            "date": "2026-11-20", "opening_assets": 300000,
            "peak_assets": 303000,
            "account_baseline": {
                "total_assets": 300000,
                "positions": {"000001": {
                    "shares": 1, "current_price": 10000,
                }},
            },
        }
        metrics, changed = restore_daily_account_metrics(
            Api(), risk_state, "2026-11-20"
        )
        self.assertTrue(changed)
        self.assertAlmostEqual(metrics["daily_return_pct"], 1.0)
        self.assertEqual(risk_state["daily_return_pct"], 1.0)

    def test_cumulative_return_dilutes_on_deposit_but_twr_holds(self):
        risk_state = {}
        metrics = update_cumulative_account_metrics(
            risk_state, 304780, realized_net_profit=4780
        )
        self.assertEqual(risk_state["total_net_capital"], 300000)
        self.assertAlmostEqual(metrics["cumulative_return_pct"], 1.5933, places=3)
        self.assertAlmostEqual(metrics["twr_return_pct"], 1.5933, places=3)

        diluted = update_cumulative_account_metrics(
            risk_state, 404780, external_flow=100000
        )
        self.assertEqual(risk_state["total_net_capital"], 400000)
        self.assertAlmostEqual(diluted["cumulative_return_pct"], 1.195, places=3)
        self.assertAlmostEqual(diluted["twr_return_pct"], 1.5933, places=3)

        after_gain = update_cumulative_account_metrics(risk_state, 408826.78)
        self.assertAlmostEqual(after_gain["twr_return_pct"], 2.6093, places=3)

    def test_cumulative_fields_survive_daily_risk_reset(self):
        risk_state = {
            "date": "2026-11-20", "opening_assets": 300000,
            "total_net_capital": 400000, "twr_factor": 1.0159,
            "twr_period_start_assets": 404780,
            "cumulative_return_pct": 1.195, "twr_return_pct": 1.5933,
            "daily_return_pct": 0.5, "halted": True,
        }
        reset_state = {
            **preserve_cumulative_risk_fields(risk_state),
            "date": "2026-11-21", "opening_assets": 405000,
            "peak_assets": 405000, "halted": False,
            "account_baseline": {"total_assets": 405000, "positions": {}},
        }
        self.assertEqual(reset_state["total_net_capital"], 400000)
        self.assertEqual(reset_state["twr_factor"], 1.0159)
        self.assertEqual(reset_state["twr_period_start_assets"], 404780)
        self.assertNotIn("daily_return_pct", reset_state)
        self.assertFalse(reset_state["halted"])

        metrics = update_cumulative_account_metrics(reset_state, 405000)
        self.assertAlmostEqual(metrics["cumulative_return_pct"], 1.25, places=3)
        self.assertAlmostEqual(metrics["twr_return_pct"], 1.6452, places=3)

    def test_daily_restore_returns_cumulative_metrics(self):
        class Api:
            def get_account_snapshot(self):
                return {"ok": True, "total_assets": 314000,
                        "positions": {"000001": {
                            "shares": 1, "current_price": 14000,
                        }}}
        risk_state = {
            "date": "2026-11-20", "opening_assets": 300000,
            "peak_assets": 314000,
            "account_baseline": {
                "total_assets": 300000,
                "positions": {"000001": {
                    "shares": 1, "current_price": 12000,
                }},
            },
        }
        with patch("bot.load_json", return_value=[
            {"type": "SELL", "net_profit_krw": 14000}
        ]):
            metrics, changed = restore_daily_account_metrics(
                Api(), risk_state, "2026-11-20"
            )
        self.assertTrue(changed)
        self.assertEqual(risk_state["total_net_capital"], 300000)
        self.assertAlmostEqual(metrics["cumulative_return_pct"], 4.6667, places=3)
        self.assertAlmostEqual(metrics["twr_return_pct"], 4.6667, places=3)

    def test_dashboard_shows_cumulative_and_twr_without_amounts(self):
        class Feed:
            def quote(self, code, *_):
                return None
            def is_healthy(self):
                return True, ""
        context = {
            "svg_returns": True,
            "daily_return_pct": 0.52,
            "cumulative_return_pct": 1.59,
            "twr_return_pct": 1.83,
            "account_drawdown_pct": 0.0,
            "total_net_capital": 300000,
        }
        markdown = render_dashboard(
            "running", "정규 감시 중", {}, Feed(), [], context=context
        )
        safeguards = markdown.split("## 계좌 안전장치", 1)[1].split("## ", 1)[0]
        self.assertIn(
            '<th width="76" nowrap align="center">누적 수익률</th>', safeguards
        )
        self.assertIn(
            '<th width="76" nowrap align="center">전략 수익률 (TWR)</th>', safeguards
        )
        self.assertIn('src="svg/cumulative-return-v5-p1_59.svg"', safeguards)
        self.assertIn('src="svg/twr-return-v5-p1_83.svg"', safeguards)
        self.assertIn(
            '<td width="76" nowrap align="center" valign="middle">', safeguards
        )
        self.assertNotIn("300,000", safeguards)
        self.assertNotIn("300000", safeguards)

        state = build_dashboard_state(
            "running", "정규 감시 중", {}, Feed(), context
        )
        self.assertEqual(state["cumulative_return_pct"], 1.59)
        self.assertEqual(state["twr_return_pct"], 1.83)
        serialized = json.dumps(state, ensure_ascii=False)
        self.assertNotIn("300,000", serialized)
        self.assertNotIn("300000", serialized)
        self.assertNotIn("total_net_capital", serialized)

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
        self.assertTrue(is_entry_window(datetime.datetime(2026, 11, 13, 15, 10)))
        self.assertFalse(is_entry_window(datetime.datetime(2026, 11, 13, 15, 11)))
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

    def test_etf_entry_waits_for_three_percent_adverse_gap_recovery(self):
        allowed, gap = etf_entry_gap_status(1427, 1340)
        self.assertFalse(allowed)
        self.assertAlmostEqual(gap, 1340 / 1427 - 1)
        self.assertTrue(etf_entry_gap_status(1427, 1385)[0])
        self.assertTrue(etf_entry_gap_status(100, 97)[0])
        self.assertFalse(etf_entry_gap_status(100, 96.99)[0])

    def test_multi_asset_trend_selects_strongest_eligible_asset(self):
        start = datetime.date(2026, 5, 1)
        def series(code_growth):
            return [
                {"date": (start + datetime.timedelta(days=index)).strftime("%Y%m%d"),
                 "close": 100 + index * code_growth}
                for index in range(130)
            ]

        regime, code, detail = multi_asset_trend_selection(
            {"069500": series(0.2), "379810": series(0.8)},
            now=datetime.datetime(2026, 11, 20, 10, 0),
        )
        self.assertEqual(regime, "asset")
        self.assertEqual(code, "379810")
        self.assertEqual(detail["target_name"], "KODEX 미국나스닥100")

    def test_multi_asset_trend_keeps_holding_within_switch_margin(self):
        start = datetime.date(2026, 5, 1)
        held = [
            {"date": (start + datetime.timedelta(days=index)).strftime("%Y%m%d"),
             "close": 100 + index * 0.50}
            for index in range(130)
        ]
        challenger = [
            {"date": (start + datetime.timedelta(days=index)).strftime("%Y%m%d"),
             "close": 100 + index * 0.51}
            for index in range(130)
        ]
        _, code, _ = multi_asset_trend_selection(
            {"069500": held, "379810": challenger}, held_code="069500",
            now=datetime.datetime(2026, 11, 20, 10, 0),
        )
        self.assertEqual(code, "069500")

    def test_daily_candle_cache_refetches_for_longer_etf_history(self):
        class Api:
            def __init__(self):
                self.counts = []
            def get_daily_candles(self, code, count):
                self.counts.append(count)
                return [{"date": str(index), "close": 100} for index in range(count)]
        class Feed:
            def quote(self, code, *_):
                return None

        api = Api()
        cache = {}
        now = datetime.datetime(2026, 11, 20, 10, 0)
        get_kiwoom_candles(api, Feed(), cache, "069500", now, 70)
        result = get_kiwoom_candles(api, Feed(), cache, "069500", now, 130)
        self.assertEqual(api.counts, [70, 130])
        self.assertEqual(len(result), 130)

    def test_sector_etf_selects_strong_asset_above_market(self):
        start = datetime.date(2026, 8, 1)
        def series(growth):
            return [
                {"date": (start + datetime.timedelta(days=index)).strftime("%Y%m%d"),
                 "close": 100 + index * growth}
                for index in range(70)
            ]
        regime, code, detail = sector_etf_trend_selection(
            {"091160": series(0.8), "091170": series(0.3)},
            series(0.2), now=datetime.datetime(2026, 11, 20, 10, 0),
        )
        self.assertEqual(regime, "asset")
        self.assertEqual(code, "091160")
        self.assertEqual(detail["target_name"], "KODEX 반도체")

    def test_sector_etf_waits_when_no_sector_beats_market(self):
        start = datetime.date(2026, 8, 1)
        def series(growth):
            return [
                {"date": (start + datetime.timedelta(days=index)).strftime("%Y%m%d"),
                 "close": 100 + index * growth}
                for index in range(70)
            ]
        regime, code, _ = sector_etf_trend_selection(
            {"091160": series(0.2)}, series(0.8),
            now=datetime.datetime(2026, 11, 20, 10, 0),
        )
        self.assertEqual((regime, code), ("cash", None))

    def test_sector_etf_does_not_reenter_on_same_day(self):
        trades = [{
            "timestamp": "2026-11-20 10:00:00", "type": "SELL",
            "strategy": "섹터 ETF 추세추종",
        }]
        self.assertTrue(sector_etf_traded_today(trades, "2026-11-20"))

    def test_dashboard_shows_separate_mim_pilot_state(self):
        class Feed:
            def quote(self, code, *_):
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

    def test_intraday_surge_universe_filters_and_validates_candidates(self):
        with patch.object(config, "LIVE_TRADING_ENABLED", True):
            api = KiwoomRestAPI("key", "secret", "account")

        def response(path, api_id, body):
            if api_id == "ka10023":
                return {"return_code": 0, "trde_qty_sdnin": [{
                    "stk_cd": "000001", "stk_nm": "급증주",
                    "cur_prc": "+10000", "flu_rt": "+4.00",
                    "now_trde_qty": "200000", "sdnin_rt": "+150.0",
                }]}
            if api_id == "ka10032":
                return {"return_code": 0, "trde_prica_upper": []}
            return {"return_code": 0, "lastPrice": "9000",
                    "listCount": "100000000", "marketName": "KOSPI",
                    "upName": "제조", "auditInfo": "정상",
                    "orderWarning": "0"}

        api._post_domestic = response
        with patch("kiwoom_api.time.sleep"):
            selected = api.get_intraday_surge_universe(
                8, 500_000_000_000, 5_000_000_000_000, 20000,
            )
        self.assertEqual(selected, {"급증주": "000001"})
        self.assertTrue(api.last_intraday_metadata["급증주"]["intraday_surge"])

    def test_intraday_surge_excludes_etf_after_stock_info_lookup(self):
        with patch.object(config, "LIVE_TRADING_ENABLED", True):
            api = KiwoomRestAPI("key", "secret", "account")

        api._post_domestic = unittest.mock.Mock(side_effect=[
            {"return_code": 0, "trde_qty_sdnin": [{
                "stk_cd": "069500", "stk_nm": "KODEX 200",
                "cur_prc": "+10000", "flu_rt": "+2.00",
                "sdnin_rt": "+100.0",
            }]},
            {"return_code": 0, "trde_prica_upper": []},
            {"return_code": 0, "lastPrice": "9000",
             "listCount": "100000000", "marketName": "ETF",
             "auditInfo": "정상", "orderWarning": "0"},
        ])
        with patch("kiwoom_api.time.sleep"):
            selected = api.get_intraday_surge_universe(
                8, 500_000_000_000, 5_000_000_000_000, 20000,
            )
        self.assertEqual(selected, {})

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
        self.assertAlmostEqual(cap, 16236.287878787878)

    def test_intraday_surge_is_limited_to_fifteen_percent(self):
        account = {"ok": True, "total_assets": 300000, "cash": 300000}
        self.assertAlmostEqual(intraday_surge_max_price(account), 15000)
        signals = [{"name": "급증주", "strategy": "변동성 돌파 (추세)",
                    "size_multiplier": 1.0}]
        limited = apply_intraday_surge_limits(signals, {"급증주"})[0]
        self.assertEqual(limited["strategy"], "당일 거래량 급증 VBO (15%)")
        self.assertAlmostEqual(limited["max_investment_pct"], 0.15)
        self.assertLess(limited["size_multiplier"], 0.5)

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

    def test_etf_rotation_holds_trend_without_tight_trailing(self):
        pos = {"buy_price": 10000, "stop_loss_pct": 0.05,
               "highest_price": 10000,
               "strategy": "시장 ETF 3상태 추세전환"}
        for price in (10200, 10500, 11000, 12000):
            stop, reason = update_position_stop(pos, price)
            self.assertAlmostEqual(stop, 9500)
            self.assertEqual(reason, "ETF 재해 손절 (-5.0%)")
        self.assertFalse(pos.get("trailing_active", False))
        self.assertNotIn("trailing_stop_price", pos)
        self.assertEqual(pos["highest_price"], 12000)

    def test_etf_rotation_keeps_previously_locked_protection(self):
        # 과거 버전에서 +3% 보호선이 걸린 보유분은 손절선을 낮추지 않는다.
        pos = {"buy_price": 1340, "stop_loss_pct": 0.025,
               "highest_price": 1412, "trailing_stop_price": 1380.2,
               "strategy": "시장 ETF 3상태 추세전환"}
        stop, reason = update_position_stop(pos, 1400)
        self.assertAlmostEqual(stop, 1380.2)
        self.assertEqual(reason, "ETF 기존 보호선")
        self.assertAlmostEqual(update_position_stop(pos, 1500)[0], 1380.2)

    def test_stock_stop_logic_is_unchanged_for_existing_positions(self):
        pos = {"buy_price": 21950, "stop_loss_pct": 0.04,
               "trailing_gap_pct": 0.04, "highest_price": 21950,
               "strategy": "RSI 과매도 반등"}
        stop, reason = update_position_stop(pos, 21950)
        self.assertAlmostEqual(stop, 21072)
        self.assertEqual(reason, "동적 손절 (-4.0% ATR 보정)")

    def test_etf_risk_budget_allows_thirty_percent_allocation(self):
        shares = calculate_entry_shares(
            300000, 300000, 10000, 0.05, risk_pct=0.015
        )
        self.assertEqual(shares, 9)
        # 기본(개별주) 위험 한도는 그대로 0.7%다.
        self.assertEqual(calculate_entry_shares(300000, 300000, 10000, 0.05), 4)

    def test_etf_rotation_can_use_fifty_percent_allocation(self):
        result = build_etf_tradeability(
            {"069500": "KODEX 200"}, "다중자산 ETF", 0.50, 0.05,
            300000, 300000, {"069500": {"price": 10000}}, risk_pct=0.025,
        )
        self.assertEqual(result[0]["max_shares"], 15)
        # 개별주 기본 한도(35%)는 그대로다.
        self.assertEqual(calculate_entry_shares(300000, 300000, 10000, 0.01), 10)

    def test_stock_entries_only_near_close(self):
        self.assertFalse(is_stock_entry_window(datetime.datetime(2026, 11, 26, 9, 30)))
        self.assertFalse(is_stock_entry_window(datetime.datetime(2026, 11, 26, 14, 49)))
        self.assertTrue(is_stock_entry_window(datetime.datetime(2026, 11, 26, 14, 50)))
        self.assertTrue(is_stock_entry_window(datetime.datetime(2026, 11, 26, 15, 10)))
        self.assertFalse(is_stock_entry_window(datetime.datetime(2026, 11, 26, 15, 11)))

    def test_stock_strategies_disabled_blocks_all_stock_entries(self):
        late = datetime.datetime(2026, 11, 26, 15, 0)
        with patch("bot.STOCK_STRATEGIES_ENABLED", False):
            self.assertIn("비활성화", stock_entry_block_reason(late, "069500", {}))

    @patch("bot.STOCK_STRATEGIES_ENABLED", True)
    def test_inverse_etf_blocks_stock_entries(self):
        late = datetime.datetime(2026, 11, 26, 15, 0)
        held_inverse = {"KODEX 인버스 추세": {"code": "114800"}}
        self.assertIn("인버스", stock_entry_block_reason(late, None, held_inverse))
        self.assertIn("인버스", stock_entry_block_reason(late, "114800", {}))
        self.assertIsNone(stock_entry_block_reason(late, "069500", {}))
        self.assertIn(
            "진입 시간",
            stock_entry_block_reason(datetime.datetime(2026, 11, 26, 10, 0), None, {}),
        )

    def test_parking_keeps_two_percent_cash(self):
        # 총자산 300,000 중 현금 200,000: 2%(6,000)만 남기고 194,000 편입 → 3주
        self.assertEqual(parking_buy_shares(300000, 200000, 55000), 3)
        # 현금 60,000: 2%를 빼도 54,000이라 55,000원짜리 1주는 못 산다
        self.assertEqual(parking_buy_shares(300000, 60000, 55000), 0)
        # 10% 버퍼였다면 못 샀을 금액도 이제 편입된다(390,000 중 현금 64,000 → 1주)
        self.assertEqual(parking_buy_shares(390000, 64000, 55000), 1)

    def test_parking_counts_as_cash_for_strategy_sizing(self):
        parking = {"shares": 3, "avg_price": 55000}
        account = {"ok": True, "total_assets": 300000, "cash": 40000}
        effective = account_with_parking_cash(account, parking, 55000)
        self.assertAlmostEqual(effective["cash"], 40000 + 3 * 55000 * 0.998)
        self.assertEqual(account["cash"], 40000)
        self.assertEqual(
            account_with_parking_cash({"ok": False}, parking, 55000), {"ok": False}
        )
        self.assertEqual(parking_liquidation_value({}, 55000), 0)

    def test_parking_sells_only_what_the_buy_needs(self):
        self.assertEqual(parking_sell_shares_needed(60000, 55000, 3), 2)
        self.assertEqual(parking_sell_shares_needed(10000, 55000, 3), 1)
        self.assertEqual(parking_sell_shares_needed(500000, 55000, 3), 3)
        self.assertEqual(parking_sell_shares_needed(0, 55000, 3), 0)

    def test_parking_ledger_tracks_average_price(self):
        parking = apply_parking_fill({}, "buy", 2, 55000, "2026-11-26")
        parking = apply_parking_fill(parking, "buy", 2, 55100, "2026-11-27")
        self.assertEqual(parking["shares"], 4)
        self.assertAlmostEqual(parking["avg_price"], 55050)
        parking = apply_parking_fill(parking, "sell", 4, 55200, "2026-11-28")
        self.assertEqual(parking["shares"], 0)
        self.assertNotIn("avg_price", parking)
        self.assertEqual(parking_reference_price(parking), 55200)
        self.assertEqual(parking_reference_price(parking, {"price": 55300}), 55300)

    def test_parking_runs_after_stock_entries_before_closing_auction(self):
        self.assertFalse(is_parking_window(datetime.datetime(2026, 11, 26, 15, 10)))
        self.assertTrue(is_parking_window(datetime.datetime(2026, 11, 26, 15, 12)))
        self.assertTrue(is_parking_window(datetime.datetime(2026, 11, 26, 15, 18)))
        self.assertFalse(is_parking_window(datetime.datetime(2026, 11, 26, 15, 19)))

    def test_parking_dashboard_hides_amounts(self):
        info = parking_dashboard_info({"shares": 3, "avg_price": 55000}, 55550, 500000)
        self.assertEqual(set(info), {"name", "code", "weight_pct", "return_pct"})
        self.assertEqual(info["weight_pct"], 33.3)
        self.assertEqual(info["return_pct"], 1.0)
        self.assertIsNone(parking_dashboard_info({}, 55550, 500000))

    def test_core_targets_do_not_overlap_other_strategies(self):
        self.assertEqual(core_code_conflicts(), [])
        self.assertAlmostEqual(
            sum(weight for _, weight in config.CORE_TARGETS.values()), 1.0
        )
        self.assertLessEqual(
            config.CORE_ALLOCATION_PCT + config.ETF_ROTATION_ALLOC_PCT,
            config.MAX_TOTAL_EXPOSURE_PCT,
        )

    def test_core_desired_shares_rounds_and_keeps_small_differences(self):
        # 목표 100,000원, 1주 30,000원 → 3주
        self.assertEqual(core_desired_shares(100000, 30000, 0, 15000), 3)
        # 1주 값이 목표의 1.5배 이내면 1주 보유, 넘으면 보유하지 않음
        self.assertEqual(core_desired_shares(20000, 26000, 0, 15000), 1)
        self.assertEqual(core_desired_shares(20000, 73000, 0, 15000), 0)
        # 차이가 최소 매매 금액보다 작으면 현재 수량 유지
        self.assertEqual(core_desired_shares(100000, 10000, 9, 15000), 9)
        self.assertEqual(core_desired_shares(100000, 10000, 13, 15000), 10)
        # 목표가 1.999주 분량이면 2주를 유지한다(버림으로 1주를 팔지 않는다).
        self.assertEqual(core_desired_shares(25586, 12800, 2, 5117), 2)
        self.assertEqual(core_desired_shares(25586, 12800, 0, 5117), 2)
        # 2.5주 이상이면 3주로 반올림한다.
        self.assertEqual(core_desired_shares(75000, 26000, 0, 15000), 3)

    def test_core_plan_sells_before_buys_and_skips_unaffordable(self):
        prices = {"161510": 20830, "458730": 15010, "367380": 26000, "365780": 87000,
                  "319640": 20000, "329750": 12500}
        plan = plan_core_rebalance(1_000_000, prices, {})
        self.assertEqual(
            [(side, code, qty) for side, code, _, qty, _ in plan],
            [("buy", "365780", 2), ("buy", "319640", 4), ("buy", "367380", 3),
             ("buy", "329750", 6), ("buy", "161510", 2), ("buy", "458730", 2)],
        )
        held = {"holdings": {"365780": {"shares": 5, "avg_price": 87000}}}
        plan = plan_core_rebalance(1_000_000, prices, held)
        self.assertEqual(plan[0][:2], ("sell", "365780"))
        self.assertEqual(plan[0][3], 3)
        # 소액 계좌에서는 1주가 목표의 1.5배를 넘는 종목을 건너뛴다.
        small = plan_core_rebalance(300_000, prices, {})
        self.assertNotIn("161510", [code for _, code, _, _, _ in small])
        # 시세가 하나라도 없으면 계획을 만들지 않는다.
        self.assertIsNone(plan_core_rebalance(1_000_000, {**prices, "319640": None}, {}))

    def test_core_ledger_and_monthly_schedule(self):
        state = apply_core_fill({}, "buy", "319640", 3, 20000)
        state = apply_core_fill(state, "buy", "319640", 1, 24000)
        self.assertEqual(core_shares(state, "319640"), 4)
        self.assertAlmostEqual(state["holdings"]["319640"]["avg_price"], 21000)
        state = apply_core_fill(state, "sell", "319640", 4, 25000)
        self.assertEqual(core_shares(state, "319640"), 0)
        self.assertNotIn("319640", state["holdings"])
        now = datetime.datetime(2026, 12, 1, 10, 30)
        self.assertTrue(core_rebalance_due({}, now))
        self.assertTrue(core_rebalance_due({"last_rebalance_month": "2026-11"}, now))
        self.assertFalse(core_rebalance_due({"last_rebalance_month": "2026-12"}, now))
        self.assertTrue(is_core_rebalance_window(now))
        self.assertFalse(is_core_rebalance_window(datetime.datetime(2026, 12, 1, 9, 30)))
        self.assertFalse(is_core_rebalance_window(datetime.datetime(2026, 12, 1, 15, 0)))

    def test_core_dashboard_hides_amounts(self):
        state = {"holdings": {"319640": {"shares": 3, "avg_price": 20000}},
                 "last_rebalance_month": "2026-12"}
        info = core_dashboard_info(state, {"319640": 21000}, 1_000_000)
        gold = next(item for item in info["assets"] if "골드" in item["name"])
        self.assertEqual(set(gold), {"name", "code", "held", "days_held", "target_pct", "weight_pct", "return_pct"})
        self.assertEqual(gold["target_pct"], 7.5)
        self.assertEqual(gold["weight_pct"], 6.3)
        self.assertEqual(gold["return_pct"], 5.0)

    def test_positions_table_includes_core_and_parking_holdings(self):
        state = {"holdings": {"319640": {"shares": 3, "avg_price": 20000}},
                 "last_rebalance_month": "2026-12"}
        context = {
            "core": core_dashboard_info(state, {"319640": 21000}, 1_000_000),
            "parking": parking_dashboard_info(
                {"shares": 3, "avg_price": 55000}, 55550, 500000
            ),
        }
        rows = dashboard_long_term_rows(context)
        self.assertEqual([row["strategy"] for row in rows], ["코어 자산배분", "대기자금"])
        self.assertIn("골드", rows[0]["name"])
        self.assertEqual(rows[0]["days_held"], 0)
        self.assertEqual(dashboard_holding_count_label({"A": {}}, context),
                         "매매 1 · 코어 1 · 대기 1")
        self.assertEqual(dashboard_holding_count_label({}, {}), "매매 0")


    def test_position_weights_are_percent_only(self):
        class RT:
            def quote(self, code, *_):
                return {"price": 1400} if code == "114800" else None
        positions = {"인버스": {"code": "114800", "shares": 69, "buy_price": 1404},
                     "한화": {"code": "003530", "shares": 8, "last_price": 3450}}
        weights = position_weight_pcts(positions, RT(), 340000)
        self.assertEqual(weights, {"인버스": 28.4, "한화": 8.1})
        self.assertEqual(position_weight_pcts(positions, RT(), 0), {})
        self.assertIn("25.5% (목표 20.0%)", dashboard_weight_cell(25.5, 20.0))
        self.assertIn(">-<", dashboard_weight_cell(None))


    def test_remember_core_last_prices(self):
        state = {"holdings": {"319640": {"shares": 1, "avg_price": 20825, "last_price": 20825}}}
        self.assertTrue(remember_core_last_prices(state, {"319640": 20965, "102110": 70000}))
        self.assertEqual(state["holdings"]["319640"]["last_price"], 20965)
        self.assertFalse(remember_core_last_prices(state, {"319640": None}))
        self.assertNotIn("102110", state["holdings"])


    def test_large_external_flow_schedules_core_rebalance(self):
        import datetime as dt
        now = dt.datetime(2026, 11, 27, 10, 30)
        state = {"last_rebalance_month": "2026-11"}
        self.assertFalse(core_rebalance_due(state, now))
        self.assertFalse(mark_flow_rebalance(state, 20000, 340000))  # 10% 미만
        self.assertTrue(mark_flow_rebalance(state, 340000, 680000))   # 입금
        self.assertTrue(core_rebalance_due(state, now))
        self.assertFalse(mark_flow_rebalance(state, 340000, 680000))  # 이미 예약
        state.pop("flow_rebalance_pending")
        self.assertTrue(mark_flow_rebalance(state, -100000, 600000))  # 출금도 대상


    def test_restore_dashboard_context_keeps_display_keys_only(self):
        saved = {"etf_rotation": {"regime": "asset"}, "core": {"enabled": True},
                 "status": "running", "parking": None, "market_regimes": {}}
        restored = restore_dashboard_context(saved)
        self.assertEqual(set(restored), {"etf_rotation", "core"})
        self.assertEqual(restore_dashboard_context(None), {})


    def test_resolve_external_flow_excludes_distributions_and_dedupes(self):
        class Api:
            def __init__(self, flows):
                self.flows = flows
            def get_cash_flows(self, date):
                return self.flows
        flows = [{"id": "1", "amount": 50000, "memo": "이체입금"},
                 {"id": "2", "amount": 120, "memo": "배당금입금"}]
        state = {}
        self.assertEqual(resolve_external_flow(Api(flows), state, "2026-12-01", 50120, 400000), 50000)
        # 같은 날 다시 조회해도 이미 반영한 내역은 다시 세지 않는다.
        self.assertEqual(resolve_external_flow(Api(flows), state, "2026-12-01", 120, 400000), 0)
        # 분배금만 들어온 날은 외부 입금이 아니다.
        self.assertEqual(resolve_external_flow(
            Api([{"id": "9", "amount": 300, "memo": "분배금입금"}]), {}, "2026-12-02", 300, 400000), 0)
        # 조회 실패 시 기존 추정치를 쓴다.
        self.assertEqual(resolve_external_flow(Api(None), {}, "2026-12-02", 7000, 400000), 7000)
        self.assertEqual(resolve_external_flow(Api(flows), {}, "2026-12-02", 0, 400000), 0)

    def test_inverse_rotation_alloc_is_capped(self):
        self.assertEqual(rotation_alloc_pct("114800"), 0.40)
        self.assertEqual(rotation_alloc_pct("069500"), 0.40)
        with mock.patch("bot.ETF_INVERSE_MAX_ALLOC_PCT", 0.15):
            self.assertEqual(rotation_alloc_pct("114800"), 0.15)



    def test_dashboard_sync_builds_tree_without_pushing(self):
        import subprocess
        import bot
        real_run = subprocess.run
        calls = []

        def fake_run(cmd, *args, **kwargs):
            calls.append(cmd[:2])
            if cmd[:2] == ["git", "push"]:
                class Result:
                    stdout = ""
                    returncode = 0
                return Result()
            return real_run(cmd, *args, **kwargs)

        class RT:
            def quote(self, code, *_):
                return None

            def is_healthy(self):
                return True, ""

        import tempfile
        tmp = tempfile.mkdtemp()
        saved = (bot.AUTO_GIT_PUSH, bot.DASHBOARD_FILE, bot.DASHBOARD_STATE_FILE)
        bot.AUTO_GIT_PUSH = True
        bot.DASHBOARD_FILE = os.path.join(tmp, "DASHBOARD.md")
        bot.DASHBOARD_STATE_FILE = os.path.join(tmp, "dashboard_state.json")
        bot.subprocess.run = fake_run
        try:
            self.assertTrue(bot.sync_dashboard_to_branch("running", "test", {}, RT(), {}))
        finally:
            bot.subprocess.run = real_run
            bot.AUTO_GIT_PUSH, bot.DASHBOARD_FILE, bot.DASHBOARD_STATE_FILE = saved
        self.assertIn(["git", "push"], calls)


    def test_positions_table_lists_shortest_holding_first(self):
        import bot

        class RT:
            def quote(self, code, *_):
                return None

            def is_healthy(self):
                return True, ""

        positions = {
            "오래된": {"code": "000001", "buy_price": 100, "last_price": 100, "shares": 1,
                     "days_held": 9, "strategy": "RSI 과매도 반등"},
            "최근": {"code": "000002", "buy_price": 100, "last_price": 100, "shares": 1,
                   "days_held": 1, "strategy": "RSI 과매도 반등"},
        }
        context = {"core": {"enabled": True, "assets": [
            {"name": "코어", "code": "000003", "held": True, "days_held": 3,
             "target_pct": 7.5, "weight_pct": 7.0, "return_pct": 0.0}]}}
        md = bot.render_dashboard("running", "x", positions, RT(), [], context=context)
        table = md.split("## 현재 보유 포지션", 1)[1].split("## ", 1)[0]
        self.assertLess(table.index("<td>최근</td>"), table.index("<td>코어</td>"))
        self.assertLess(table.index("<td>코어</td>"), table.index("<td>오래된</td>"))


    def test_deposit_during_trade_is_caught_from_history(self):
        class Api:
            def get_cash_flows(self, date):
                return [{"id": "7", "amount": 30000, "memo": "이체입금"}]
        prev = {"positions": {"114800": {"shares": 69}}}
        after_trade = {"positions": {"114800": {"shares": 0}}}
        self.assertTrue(holdings_changed(prev, after_trade))
        self.assertFalse(holdings_changed(prev, prev))
        # 매매로 잔고 추정이 0이어도 입출금 내역의 입금을 반영한다.
        self.assertEqual(resolve_external_flow(Api(), {}, "2026-12-01", 0, 400000, traded=True), 30000)
        # 매매도 없고 추정도 0이면 조회하지 않는다.
        self.assertEqual(resolve_external_flow(Api(), {}, "2026-12-01", 0, 400000), 0)


    def test_dashboard_hides_stock_sections_when_disabled(self):
        import bot

        class RT:
            def quote(self, code, *_):
                return None

            def is_healthy(self):
                return True, ""

        with mock.patch("bot.STOCK_STRATEGIES_ENABLED", False):
            md = bot.render_dashboard("running", "x", {}, RT(), [], context={})
        self.assertNotIn("## 감시 종목 전체", md)
        self.assertNotIn("| RSI 반등", md)


    def test_rotation_resize_after_large_deposit(self):
        pos = {"shares": 69, "buy_price": 1404.0}
        # 3천만 원 입금 뒤 총자산 3,039만 원: 40% 목표까지 추가 매수
        side, qty = plan_rotation_resize(pos, 1356, 30_390_000, 0.40)
        self.assertEqual(side, "buy")
        self.assertEqual(qty, round((30_390_000 * 0.40 - 69 * 1356) / 1356))
        # 목표 대비 10% 미만 차이는 매매하지 않음(작은 입금·평소 물타기 방지)
        self.assertIsNone(plan_rotation_resize({"shares": 100}, 1000, 260_000, 0.40))
        # 낙폭 구간에서는 늘리지 않음
        self.assertIsNone(plan_rotation_resize(pos, 1356, 30_390_000, 0.40, 0.5))
        # 큰 출금이면 줄이되 전량 매도는 하지 않음
        side, qty = plan_rotation_resize({"shares": 100}, 1000, 100_000, 0.40)
        self.assertEqual((side, qty), ("sell", 60))
        side, qty = plan_rotation_resize({"shares": 2}, 1000, 1_000, 0.40)
        self.assertEqual((side, qty), ("sell", 1))
        avg = apply_rotation_resize_fill({"shares": 69, "buy_price": 1404.0}, "buy", 31, 1300)
        self.assertEqual(avg["shares"], 100)
        self.assertAlmostEqual(avg["buy_price"], (69 * 1404 + 31 * 1300) / 100, places=3)

    def test_large_flow_marks_rotation_resize(self):
        state = {}
        self.assertTrue(mark_flow_rebalance(state, 30_000_000, 30_390_000))
        self.assertTrue(state.get("rotation_resize_pending"))
        small = {}
        self.assertFalse(mark_flow_rebalance(small, 10_000, 400_000))
        self.assertNotIn("rotation_resize_pending", small)


if __name__ == "__main__":
    unittest.main()

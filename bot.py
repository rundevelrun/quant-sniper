import os
import time
import json
import datetime
import math
import subprocess
import fcntl
import html
from env_loader import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

from config import (
    DYNAMIC_SCREENING, SCREENER_TOP_N, SCREENER_RANK_POOL, DEFAULT_UNIVERSE,
    STOP_LOSS_PCT, TRAILING_STOP_TRIGGER, TRAILING_STOP_LOCK,
    TAKE_PROFIT_PCT, MAX_HOLD_DAYS, MAX_PROFIT_HOLD_DAYS,
    MAX_ALLOC_PER_STOCK, MAX_POSITIONS,
    DAILY_KILL_SWITCH_PCT, POLL_INTERVAL_SECONDS, API_REQUEST_DELAY,
    TOTAL_ROUNDTRIP_COST_PCT, TRANSACTION_TAX_PCT, BROKER_COMMISSION_PCT,
    SLIPPAGE_ESTIMATE_PCT, ORDER_CONFIRM_TIMEOUT_SECONDS,
    MARKET_PROXY_CODE, KOSDAQ_MARKET_PROXY_CODE,
    ENTRY_START_TIME, ENTRY_END_TIME, AUTO_GIT_PUSH,
    MIN_MARKET_CAP_KRW, MAX_MARKET_CAP_KRW, MAX_RISK_PER_TRADE_PCT,
    EQUITY_SLOWDOWN_DRAWDOWN_PCT, EQUITY_HALT_DRAWDOWN_PCT,
    MAX_POSITIONS_PER_SECTOR, ATR_STOP_MULTIPLIER,
    MIN_STOP_LOSS_PCT, MAX_STOP_LOSS_PCT, MAX_TOTAL_EXPOSURE_PCT,
    MAX_ENTRY_DAY_RETURN_PCT, MIM_ENABLED, MIM_ENTRY_START_TIME,
    MIM_ENTRY_END_TIME, MIM_EXIT_TIME, MIM_MAX_SHARES, MIM_STOP_LOSS_PCT,
    ETF_ROTATION_ENABLED, ETF_ROTATION_LONG_CODE,
    ETF_ROTATION_INVERSE_CODE, ETF_ROTATION_MA_PERIOD,
    ETF_ROTATION_SLOPE_DAYS, ETF_ROTATION_BAND_PCT,
    ETF_ROTATION_ASSETS, ETF_ROTATION_SHORT_MOMENTUM_DAYS,
    ETF_ROTATION_LONG_MOMENTUM_DAYS, ETF_ROTATION_SWITCH_MARGIN,
    ETF_ROTATION_ALLOC_PCT, ETF_ROTATION_STOP_LOSS_PCT,
    ETF_MAX_ADVERSE_ENTRY_GAP_PCT, ETF_INVERSE_CODES, ETF_INVERSE_MAX_ALLOC_PCT,
    ETF_MAX_RISK_PER_TRADE_PCT, ETF_SLIPPAGE_ESTIMATE_PCT,
    STOCK_ENTRY_START_TIME, STOCK_ENTRY_END_TIME,
    BLOCK_STOCK_ENTRIES_WHEN_INVERSE, STOCK_STRATEGIES_ENABLED,
    PARKING_ENABLED, PARKING_ETF_CODE, PARKING_ETF_NAME,
    PARKING_START_TIME, PARKING_END_TIME, PARKING_MIN_KRW,
    PARKING_VALUE_HAIRCUT_PCT, PARKING_CASH_RESERVE_PCT,
    CORE_ALLOCATION_ENABLED, CORE_ALLOCATION_PCT, CORE_TARGETS,
    CORE_REBALANCE_START_TIME, CORE_REBALANCE_END_TIME,
    CORE_MIN_TRADE_PCT, CORE_ONE_SHARE_TOLERANCE, CORE_FLOW_REBALANCE_PCT,
    SECTOR_ETF_ENABLED, SECTOR_ETF_ASSETS, SECTOR_ETF_MA_PERIOD,
    SECTOR_ETF_SLOPE_DAYS, SECTOR_ETF_BAND_PCT,
    SECTOR_ETF_SHORT_MOMENTUM_DAYS, SECTOR_ETF_LONG_MOMENTUM_DAYS,
    SECTOR_ETF_SWITCH_MARGIN, SECTOR_ETF_ALLOC_PCT,
    SECTOR_ETF_STOP_LOSS_PCT,
    INTRADAY_SURGE_ENABLED, INTRADAY_SURGE_SCAN_INTERVAL_SECONDS,
    INTRADAY_SURGE_MAX_CANDIDATES, INTRADAY_SURGE_MIN_DAY_RETURN_PCT,
    INTRADAY_SURGE_MAX_DAY_RETURN_PCT, INTRADAY_SURGE_MIN_VOLUME_RATE_PCT,
    INTRADAY_SURGE_MAX_INVESTMENT_PCT, INTRADAY_SURGE_SIZE_MULTIPLIER
)
from strategy import calculate_atr, evaluate_signals, is_favorable_market
from kiwoom_api import KiwoomRestAPI
from kiwoom_realtime import KiwoomRealtimeFeed
from research_store import record_research_snapshot

TRADES_FILE = os.path.join(BASE_DIR, "trades.json")
POSITIONS_FILE = os.path.join(BASE_DIR, "positions.json")
HEALTH_FILE = os.path.join(BASE_DIR, "health.json")
UNIVERSE_FILE = os.path.join(BASE_DIR, "universe.json")
RISK_FILE = os.path.join(BASE_DIR, "risk_state.json")
DASHBOARD_FILE = os.path.join(BASE_DIR, "DASHBOARD.md")
DASHBOARD_EVENTS_FILE = os.path.join(BASE_DIR, "dashboard_events.json")
DASHBOARD_STATE_FILE = os.path.join(BASE_DIR, "dashboard_state.json")
MIM_STATE_FILE = os.path.join(BASE_DIR, "mim_state.json")
PARKING_STATE_FILE = os.path.join(BASE_DIR, "parking_state.json")
CORE_STATE_FILE = os.path.join(BASE_DIR, "core_state.json")
# 대시보드 표시용 시세 허용 나이. 주문 판단은 15초 기준을 그대로 쓰고,
# 표시만 체결이 뜸한 ETF도 최근 체결가로 수익률·비중을 보여 준다.
DISPLAY_QUOTE_MAX_AGE_SECONDS = 6 * 3600
# 장중 재시작 후 실시간 정규장 체결을 기다리는 최대 시간(이후에는 기존 장외 처리).
STARTUP_LIVE_WAIT_SECONDS = 90
# 보유 포지션 청산 판단에 쓰는 시세 허용 나이(주문 진입은 15초 기준 유지).
EXIT_QUOTE_MAX_AGE_SECONDS = 60
LOCK_FILE = os.path.join(BASE_DIR, ".quant_sniper.lock")
RESEARCH_DB_FILE = os.path.join(BASE_DIR, "research_data.db")
CURRENT_MARKET_OPEN = False
UNIVERSE_STRATEGY_VERSION = "rsi_vbo_mixed_v5"
MIM_STRATEGY = "KODEX200 장마감 30분 모멘텀"
MIM_POSITION_NAME = "KODEX200 장마감 모멘텀"
ETF_ROTATION_STRATEGY = "시장 ETF 3상태 추세전환"
SECTOR_ETF_STRATEGY = "섹터 ETF 추세추종"
INTRADAY_SURGE_STRATEGY = "당일 거래량 급증 VBO (15%)"
ETF_ROTATION_NAMES = {
    code: f"{name} 추세" for code, name in ETF_ROTATION_ASSETS.items()
}
SECTOR_ETF_NAMES = {
    code: f"{name} 추세" for code, name in SECTOR_ETF_ASSETS.items()
}
ETF_ROUNDTRIP_COST_PCT = (BROKER_COMMISSION_PCT * 2) + (
    ETF_SLIPPAGE_ESTIMATE_PCT * 2
)
NON_STOCK_STRATEGIES = {MIM_STRATEGY, ETF_ROTATION_STRATEGY, SECTOR_ETF_STRATEGY}

def acquire_instance_lock():
    lock_handle = open(LOCK_FILE, "w", encoding="utf-8")
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock_handle.close()
        raise RuntimeError("quant-sniper가 이미 실행 중입니다.") from exc
    lock_handle.write(str(os.getpid()))
    lock_handle.flush()
    return lock_handle

def update_health_status(status="healthy", detail=""):
    """봇 생존 및 헬스체크 상태 로컬 파일 갱신 (1분 주기, 150바이트 고정)"""
    health_data = {
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": status,
        "detail": detail,
        "pid": os.getpid(),
        "is_market_open": CURRENT_MARKET_OPEN
    }
    save_json(HEALTH_FILE, health_data)

def load_json(filepath, default):
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default

def save_json(filepath, data):
    temp_path = f"{filepath}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_path, filepath)

def is_dashboard_heartbeat_due(last_push_time, now=None):
    """매시 00분 10초와 30분 10초가 지난 뒤 정기 갱신 여부를 반환한다."""
    now = now or datetime.datetime.now()
    slot_minute = 30 if now.minute >= 30 else 0
    latest_slot = now.replace(
        minute=slot_minute, second=10, microsecond=0
    )
    if now < latest_slot:
        latest_slot -= datetime.timedelta(minutes=30)
    return last_push_time < latest_slot.timestamp()

def is_dashboard_five_minute_due(last_push_time, now=None):
    """매 5분 단위의 10초 시점이 지난 뒤 정기 갱신 여부를 반환한다."""
    now = now or datetime.datetime.now()
    slot_minute = (now.minute // 5) * 5
    latest_slot = now.replace(minute=slot_minute, second=10, microsecond=0)
    if now < latest_slot:
        latest_slot -= datetime.timedelta(minutes=5)
    return last_push_time < latest_slot.timestamp()

def update_dashboard_events(events, event, now=None, retention_days=3):
    """최근 3일 이벤트만 유지하고 반복 스킵은 한 항목의 횟수로 합친다."""
    now = now or datetime.datetime.now()
    cutoff = now - datetime.timedelta(days=retention_days)
    retained = []
    for item in events:
        try:
            timestamp = datetime.datetime.fromisoformat(item.get("timestamp", ""))
        except (TypeError, ValueError):
            continue
        if timestamp >= cutoff:
            retained.append(dict(item))

    event = dict(event)
    event.setdefault("timestamp", now.isoformat(timespec="seconds"))
    dedupe_key = (event.get("type"), event.get("code"), event.get("reason"))
    if event.get("type") == "SKIP":
        for item in reversed(retained):
            item_key = (item.get("type"), item.get("code"), item.get("reason"))
            if item_key == dedupe_key:
                item["timestamp"] = event["timestamp"]
                item["count"] = int(item.get("count", 1)) + 1
                return retained[-500:]
    event.setdefault("count", 1)
    retained.append(event)
    return retained[-500:]

def record_dashboard_event(event_type, name="", code="", reason="", **details):
    event = {
        "type": event_type, "name": name, "code": code,
        "reason": reason, **details,
    }
    events = update_dashboard_events(
        load_json(DASHBOARD_EVENTS_FILE, []), event
    )
    save_json(DASHBOARD_EVENTS_FILE, events)
    return events

def _md(value):
    return str(value if value is not None else "-").replace("|", "\\|").replace("\n", " ")

def return_pct_svg(value):
    """GitHub에서 그대로 렌더링되는 투명 배경 수익률 SVG를 만든다."""
    value = float(value)
    text = f"{value:+.2f}%"
    color = "#cf222e" if value > 0 else "#0969da" if value < 0 else "#57606a"
    # GitHub 표는 좁은 셀의 이미지를 max-width:100%로 축소하므로,
    # 모든 수익률 자산을 가장 좁은 열에도 들어가는 동일 크기로 고정한다.
    width = 44
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="22" '
        f'viewBox="0 0 {width} 22" role="img" aria-label="{html.escape(text)}">'
        f'<text x="{width - 2}" y="14" text-anchor="end" fill="{color}" '
        'font-family="system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif" '
        f'font-size="16" font-weight="700" textLength="40" '
        f'lengthAdjust="spacingAndGlyphs">{html.escape(text)}</text></svg>'
    )

def dashboard_return(value, asset_name, context, html_image=False):
    if value is None:
        return "-"
    numeric_value = float(value)
    text = f"{numeric_value:+.2f}%"
    if not context.get("svg_returns"):
        return text
    value_token = f"{numeric_value:+.2f}".replace("+", "p").replace("-", "m").replace(".", "_")
    versioned_name = f"{asset_name}-v5-{value_token}"
    context.setdefault("svg_assets", {})[versioned_name] = return_pct_svg(numeric_value)
    if html_image:
        return (
            f'<img src="svg/{versioned_name}.svg" alt="{html.escape(text)}" '
            'width="44" height="22" align="middle">'
        )
    return f"![{text}](svg/{versioned_name}.svg)"

RETURN_COLUMN_WIDTH = 76

def dashboard_return_cell(value, asset_name, context):
    """모든 수익률 SVG를 동일 폭의 가운데 정렬 셀에 배치한다."""
    return (
        f'<td width="{RETURN_COLUMN_WIDTH}" nowrap align="center" valign="middle">'
        f'{dashboard_return(value, asset_name, context, html_image=True)}</td>'
    )

def order_dashboard_sections(lines, order):
    """완성된 Markdown 섹션을 지정된 운영 우선순위로 재배치한다."""
    starts = [index for index, line in enumerate(lines) if line.startswith("## ")]
    if not starts:
        return lines
    prefix = lines[:starts[0]]
    sections = {}
    for offset, start in enumerate(starts):
        end = starts[offset + 1] if offset + 1 < len(starts) else len(lines)
        sections[lines[start][3:]] = lines[start:end]
    ordered = list(prefix)
    for title in order:
        section = sections.pop(title, [])
        if section and ordered and ordered[-1] != "":
            ordered.append("")
        ordered.extend(section)
    for section in sections.values():
        if section and ordered and ordered[-1] != "":
            ordered.append("")
        ordered.extend(section)
    return ordered

def dashboard_status_label(status):
    """운영 상태를 GitHub 대시보드용 신호등 레이블로 변환한다."""
    if status == "running":
        return "🟢 정상"
    if status == "halted":
        return "🔴 거래 중단"
    if status == "error":
        return "🔴 오류"
    return f"🟡 {_md(status)}"

def dashboard_weight_cell(weight_pct, target_pct=None):
    """보유 비중(계좌 평가자산 대비 %) 셀. 코어는 목표 비중을 함께 표시한다."""
    text = f"{float(weight_pct):.1f}%" if weight_pct is not None else "-"
    if target_pct is not None:
        text += f" (목표 {float(target_pct):.1f}%)"
    return f'<td align="right" nowrap>{html.escape(text)}</td>'

def position_weight_pcts(positions, realtime, total_assets):
    """매매 포지션별 계좌 대비 비중(%)만 계산한다(금액은 공개하지 않음)."""
    total_assets = float(total_assets or 0)
    weights = {}
    if total_assets <= 0:
        return weights
    for name, pos in (positions or {}).items():
        quote = realtime.quote(pos.get("code", ""), DISPLAY_QUOTE_MAX_AGE_SECONDS) if realtime else None
        price = float(
            (quote or {}).get("price") or pos.get("last_price") or pos.get("buy_price") or 0
        )
        shares = int(pos.get("shares", 0) or 0)
        if price > 0 and shares > 0:
            weights[name] = round(shares * price / total_assets * 100, 1)
    return weights

RESTORABLE_DASHBOARD_KEYS = (
    "etf_rotation", "sector_etf", "market_regimes", "market_favorable",
    "core", "parking", "tradeability", "universe_date", "universe_source",
    "universe_count", "universe_symbols", "stock_entry_block_reason",
    "daily_return_pct", "cumulative_return_pct", "twr_return_pct",
    "account_drawdown_pct", "last_account_sync", "last_external_flow",
)

def restore_dashboard_context(saved_state):
    """재시작 직후(특히 장외) 대시보드가 비지 않도록 직전 게시 상태를 복원한다."""
    saved_state = saved_state or {}
    return {
        key: saved_state[key] for key in RESTORABLE_DASHBOARD_KEYS
        if saved_state.get(key) not in (None, {}, [])
    }

def dashboard_long_term_rows(context):
    """보유 포지션 표에 함께 보여 줄 코어·대기자금 보유분(비중·수익률만)."""
    context = context or {}
    rows = []
    core = context.get("core") or {}
    if core.get("enabled"):
        for item in core.get("assets", []):
            if not item.get("held"):
                continue
            rows.append({
                "name": item.get("name", "-"),
                "strategy": "코어 자산배분",
                "return_pct": item.get("return_pct"),
                "asset_key": f"core-{item.get('code', 'unknown')}",
                "days_held": item.get("days_held"),
                "weight_pct": item.get("weight_pct"),
                "target_pct": item.get("target_pct"),
                "exit_plan": "월 1회 리밸런싱(목표 비중 초과분만 매도)",
            })
    parking = context.get("parking") or {}
    if parking:
        rows.append({
            "name": parking.get("name", "-"),
            "strategy": "대기자금",
            "return_pct": parking.get("return_pct"),
            "asset_key": f"parking-{parking.get('code', 'unknown')}",
            "weight_pct": parking.get("weight_pct"),
            "target_pct": None,
            "exit_plan": "매수 자금 부족 시 필요한 만큼만 매도",
        })
    return rows

def dashboard_holding_count_label(positions, context):
    """상단 보유 수: 매매 포지션과 코어·대기자금을 구분해 표시한다."""
    core_count = sum(
        1 for item in ((context or {}).get("core") or {}).get("assets", [])
        if item.get("held")
    ) if ((context or {}).get("core") or {}).get("enabled") else 0
    parts = [f"매매 {len(positions)}"]
    if core_count:
        parts.append(f"코어 {core_count}")
    if (context or {}).get("parking"):
        parts.append("대기 1")
    return " · ".join(parts)

def render_dashboard(status, detail, positions, realtime, events, now=None, context=None):
    """GitHub에서 바로 읽을 수 있는 운영 대시보드 Markdown을 생성한다."""
    now = now or datetime.datetime.now()
    context = context or {}
    recent_events = update_dashboard_events(events, {
        "type": "_render_marker", "timestamp": now.isoformat(timespec="seconds")
    }, now)[:-1]
    counts = {kind: sum(int(e.get("count", 1)) for e in recent_events
                        if e.get("type") == kind)
              for kind in ("BUY", "SELL", "SKIP", "RISK", "ERROR")}
    sells = [e for e in recent_events if e.get("type") == "SELL"]
    returns = [float(e["net_return_pct"]) for e in sells if e.get("net_return_pct") is not None]
    wins = sum(1 for value in returns if value > 0)
    win_rate = (wins / len(returns) * 100) if returns else None
    avg_return = (sum(returns) / len(returns)) if returns else None
    all_sells = [trade for trade in context.get("all_trades", [])
                 if trade.get("type") == "SELL"]
    all_returns = [float(trade["net_return_pct"]) for trade in all_sells
                   if trade.get("net_return_pct") is not None]
    all_wins = [value for value in all_returns if value > 0]
    all_losses = [value for value in all_returns if value <= 0]
    all_win_rate = (len(all_wins) / len(all_returns) * 100) if all_returns else None
    average_win = (sum(all_wins) / len(all_wins)) if all_wins else None
    average_loss = (sum(all_losses) / len(all_losses)) if all_losses else None
    total_net_profit = sum(float(trade.get("net_profit_krw", 0)) for trade in all_sells)
    total_cost = sum(float(trade.get("cost_fee_krw", 0)) for trade in all_sells)
    total_entry_notional = sum(
        float(trade.get("entry_price", 0)) * int(trade.get("shares", 0))
        for trade in all_sells
    )
    aggregate_net_return = (
        total_net_profit / total_entry_notional * 100
        if total_entry_notional > 0 else None
    )
    aggregate_cost_rate = (
        total_cost / total_entry_notional * 100
        if total_entry_notional > 0 else None
    )
    gross_win = sum(float(trade.get("net_profit_krw", 0)) for trade in all_sells
                    if float(trade.get("net_profit_krw", 0)) > 0)
    gross_loss = abs(sum(float(trade.get("net_profit_krw", 0)) for trade in all_sells
                         if float(trade.get("net_profit_krw", 0)) < 0))
    profit_factor = (gross_win / gross_loss) if gross_loss else None
    max_loss_streak = 0
    loss_streak = 0
    for value in all_returns:
        loss_streak = loss_streak + 1 if value <= 0 else 0
        max_loss_streak = max(max_loss_streak, loss_streak)
    realtime_ok, realtime_error = realtime.is_healthy()
    commit_hash = context.get("commit_hash", "-")
    market_regimes = context.get("market_regimes", {})
    if market_regimes:
        market_label = " · ".join(
            f"{market}: {'🟢 상승' if favorable else '🔴 비우호'}"
            for market, favorable in market_regimes.items()
        )
    else:
        market_label = (
            "🟢 상승 국면 — 신규 진입 검토 가능"
            if context.get("market_favorable")
            else "🔴 비우호 국면 — 강한 RSI 50%"
        )
    rotation = context.get("etf_rotation", {})
    sector_rotation = context.get("sector_etf", {})
    etf_tradeability = context.get("etf_tradeability", [])
    etf_tradeability_by_code = {
        item.get("code"): item for item in etf_tradeability
    }
    regime_label = (
        rotation.get("target_name")
        if rotation.get("regime") == "asset"
        else "현금" if rotation.get("regime") == "cash"
        else "계산 대기"
    )
    lines = [
        *DASHBOARD_HEADER_LINES,
        f"> 마지막 갱신: **{now.strftime('%Y-%m-%d %H:%M:%S')} KST**  ",
        "> 갱신 주기: **보유 중 매 5분 10초(00·05·10…분) · 상태/신호/거래 변경 즉시 · 무보유·무변화 시 00분 10초·30분 10초**", "",
        "## 현재 상태", "",
        "| 시스템 | 장 운영 | 시장 국면 | 신규 매수 | 보유 포지션 |",
        "|---|---|---|---|---:|",
        f"| {dashboard_status_label(status)} "
        f"| {'🟢 OPEN' if CURRENT_MARKET_OPEN else '🔴 CLOSED'} "
        f"| {regime_label} "
        f"| {'가능' if context.get('entry_allowed', False) else '중단'} "
        f"| {dashboard_holding_count_label(positions, context)} |",
        "",
        f"> {_md(detail)} · 키움 실시간 {'정상' if realtime_ok else '연결 대기'}"
        f" · 마지막 계좌 확인 {_md(context.get('last_account_sync', '-'))}",
        *(
            ["", f"> 개별주: {_md(context['stock_entry_block_reason'])}"]
            if context.get("stock_entry_block_reason") else []
        ),
        *(
            ["", "> 현금 대기: {} · 계좌 비중 {} · 수익률 {}".format(
                _md(context["parking"].get("name")),
                f"{context['parking']['weight_pct']:.1f}%"
                if context["parking"].get("weight_pct") is not None else "-",
                f"{context['parking']['return_pct']:+.2f}%"
                if context["parking"].get("return_pct") is not None else "-",
            )]
            if context.get("parking") else []
        ),
        "", "## 현재 보유 포지션", "",
        '<table width="100%">',
        '<thead><tr>',
        '<th width="17%">종목</th><th width="14%">전략</th>',
        f'<th width="{RETURN_COLUMN_WIDTH}" nowrap align="center">수익률</th>',
        '<th width="12%" nowrap>비중</th><th width="8%">보유 기간</th>',
        '<th width="11%">현재 보호</th><th width="30%">청산 계획</th>',
        '</tr></thead><tbody>',
    ]
    # 보유 기간이 짧은(최근 진입) 종목이 위로 오도록 행을 모아 정렬한다.
    position_rows = []
    if positions:
        for name, pos in positions.items():
            quote = realtime.quote(pos.get("code", ""), DISPLAY_QUOTE_MAX_AGE_SECONDS)
            current = float(
                quote.get("price", 0) if quote else pos.get("last_price", 0)
            )
            buy = float(pos.get("buy_price", 0))
            return_pct = ((current - buy) / buy * 100) if current and buy else None
            strategy = pos.get("strategy", "-")
            display_name = name
            if strategy == ETF_ROTATION_STRATEGY:
                display_name = name.removesuffix(" 추세")
                display_strategy = "ETF 추세전환"
                exit_plan = (
                    f"국면 전환 · -{float(pos.get('stop_loss_pct', ETF_ROTATION_STOP_LOSS_PCT))*100:.1f}% 재해 손절"
                    + (" · 기존 보호선 유지" if pos.get("trailing_stop_price") else "")
                )
            elif strategy == SECTOR_ETF_STRATEGY:
                display_name = name.removesuffix(" 추세")
                display_strategy = "섹터 ETF 추세"
                exit_plan = (
                    f"섹터 전환 · -{float(pos.get('stop_loss_pct', SECTOR_ETF_STOP_LOSS_PCT))*100:.1f}% 재해 손절"
                    + (" · 기존 보호선 유지" if pos.get("trailing_stop_price") else "")
                )
            elif strategy == MIM_STRATEGY:
                display_strategy = "장마감 모멘텀"
                exit_plan = f"-{MIM_STOP_LOSS_PCT*100:.1f}% 손절 또는 {MIM_EXIT_TIME} 청산"
            else:
                display_strategy = strategy
                exit_plan = "ATR 손절 · +4% 보호 · +8% 추적 · 타임스탑"
            protection = (
                "고점 추적 ON" if pos.get("trailing_active")
                else "수익 보호 ON" if pos.get("trailing_stop_price")
                else "재해 손절" if strategy in {ETF_ROTATION_STRATEGY, SECTOR_ETF_STRATEGY}
                else "초기 손절"
            )
            position_rows.append((int(pos.get("days_held", 0) or 0), (
                '<tr>'
                f'<td>{html.escape(str(display_name))}</td>'
                f'<td>{html.escape(str(display_strategy))}</td>'
                f'{dashboard_return_cell(return_pct, f"position-{pos.get('code', 'unknown')}", context)}'
                f'{dashboard_weight_cell((context.get("position_weights") or {}).get(name))}'
                f'<td align="right">{int(pos.get("days_held", 0))}일</td>'
                f'<td>{html.escape(protection)}</td>'
                f'<td>{html.escape(exit_plan)}</td>'
                '</tr>'
            )))
    long_term_rows = dashboard_long_term_rows(context)
    for row in long_term_rows:
        days = int(row.get("days_held") or 0) or 10**6  # 보유 기간 미상(대기자금)은 맨 아래
        position_rows.append((days, (
            '<tr>'
            f'<td>{html.escape(str(row["name"]))}</td>'
            f'<td>{html.escape(row["strategy"])}</td>'
            f'{dashboard_return_cell(row["return_pct"], row["asset_key"], context)}'
            f'{dashboard_weight_cell(row.get("weight_pct"), row.get("target_pct"))}'
            f'<td align="right">{f"{int(row["days_held"])}일" if row.get("days_held") else "-"}</td>'
            '<td>없음(장기 보유)</td>'
            f'<td>{html.escape(row["exit_plan"])}</td>'
            '</tr>'
        )))
    lines += [html_row for _, html_row in sorted(position_rows, key=lambda item: item[0])]
    if not positions and not long_term_rows:
        lines.append('<tr><td colspan="7">보유 종목 없음</td></tr>')
    lines += ['</tbody></table>']
    if not realtime_ok and realtime_error:
        lines += ["", f"> WebSocket 상태: {_md(realtime_error)}"]

    mim_state = context.get("mim_state", {})
    lines += [
        "", "## 전략별 현재 판단", "",
        "| 전략 | 상태 | 대상 | 판단 근거 |",
        "|---|---|---|---|",
    ]
    if ETF_ROTATION_ENABLED:
        rotation_trade = etf_tradeability_by_code.get(rotation.get("target_code"), {})
        rotation_status = (
            "보유 중" if rotation.get("held")
            else "당일 재진입 제한" if rotation.get("traded_today")
            else "역방향 갭 회복 대기" if rotation.get("entry_gap_blocked")
            else "매수 불가" if rotation.get("regime") == "asset"
            and rotation_trade and not rotation_trade.get("eligible")
            else "진입 감시" if rotation.get("regime") != "cash"
            else "현금 대기"
        )
        lines.append(
            f"| ETF 추세전환 | {rotation_status} "
            f"| {regime_label} | 전일 확정 3/6개월 모멘텀·60일선·교체 완충 |"
        )
    if SECTOR_ETF_ENABLED:
        sector_label = (
            sector_rotation.get("target_name")
            if sector_rotation.get("regime") == "asset" else "현금"
        )
        sector_trade = etf_tradeability_by_code.get(
            sector_rotation.get("target_code"), {}
        )
        sector_status = (
            "보유 중" if sector_rotation.get("held")
            else "당일 재진입 제한" if sector_rotation.get("traded_today")
            else "역방향 갭 회복 대기" if sector_rotation.get("entry_gap_blocked")
            else "매수 불가" if sector_rotation.get("regime") == "asset"
            and sector_trade and not sector_trade.get("eligible")
            else "진입 감시" if sector_rotation.get("regime") == "asset"
            else "현금 대기"
        )
        lines.append(
            f"| 섹터 ETF 추세 | {sector_status} "
            f"| {sector_label} | 전일 확정 1/3개월 모멘텀·시장 상대강도·최대 15% |"
        )
    if STOCK_STRATEGIES_ENABLED:  # 꺼져 있으면 화면에서 뺀다
        lines += [
            f"| RSI 반등 | {'신호 확인 중' if int(context.get('confirming_signal_count', 0)) else '대기'} "
            f"| 감시 유니버스 | {market_label} · {STOCK_ENTRY_START_TIME}~{STOCK_ENTRY_END_TIME} 진입 |",
            f"| VBO 돌파 | {'확정 신호 있음' if int(context.get('confirmed_signal_count', 0)) else '대기'} "
            f"| 감시 유니버스 | 가격 돌파와 거래량 확인 · 우호장만 · {STOCK_ENTRY_START_TIME}~{STOCK_ENTRY_END_TIME} 진입 |",
        ]
    if INTRADAY_SURGE_ENABLED:
        lines.append(
            f"| 당일 거래량 급증 | {'후보 감시' if int(context.get('intraday_surge_count', 0)) else '대기'} "
            f"| {int(context.get('intraday_surge_count', 0))}종목 | 5분 순위 조회 · 계좌 15% · 동시 1종목 |"
        )
    if MIM_ENABLED:
        lines.append(
            f"| 장마감 모멘텀 | {'진입 신호' if mim_state.get('long_signal') else '수집/대기'} "
            f"| KODEX 200 | {MIM_EXIT_TIME} 당일 청산 |"
        )

    if STOCK_STRATEGIES_ENABLED:
        lines += [
            "", "## 감시 종목 전체", "",
            f"> 전체 {int(context.get('universe_count', 0))}개 · 신호 우선·RSI 오름차순", "",
            "| 종목 | 시장 | 직전 RSI | 현재 RSI | 신호 | 현재 판단 |",
            "|---|---|---:|---:|---|---|",
        ]
        universe_symbols = context.get("tradeability") or context.get("universe_symbols", [])
        signal_statuses = {
            item.get("name"): item for item in context.get("signal_statuses", [])
        }
        signal_diagnostics = {
            item.get("name"): item for item in context.get("signal_diagnostics", [])
        }
        if universe_symbols:
            ranked = sorted(universe_symbols, key=lambda item: (
                0 if signal_statuses.get(item.get("name"), {}).get("active") else 1,
                float(signal_diagnostics.get(item.get("name"), {}).get("previous_rsi") or 999),
            ))
            for item in ranked:
                signal = signal_statuses.get(item.get("name"), {})
                diagnostic = signal_diagnostics.get(item.get("name"), {})
                previous_rsi = diagnostic.get("previous_rsi")
                current_rsi = diagnostic.get("rsi")
                blockers = diagnostic.get("rsi_blocker") or diagnostic.get("vbo_blocker") or "계산 대기"
                signal_text = (
                    f"🟢 {_md(signal.get('strategy'))}"
                    f"{' · 15% 제한' if signal.get('strategy') in {'비우호장 강한 VBO (15%)', INTRADAY_SURGE_STRATEGY} else ' · 비우호장 50%' if float(signal.get('size_multiplier', 1.0)) < 1 else ''} "
                    f"({int(signal.get('count', 0))}/3)"
                    if signal.get("active") else "🔴 신호 없음"
                )
                lines.append(
                    f"| {_md(item.get('name'))} | {_md(item.get('market', '미분류'))} "
                    f"| {f'{float(previous_rsi):.1f}' if previous_rsi is not None else '-'} "
                    f"| {f'{float(current_rsi):.1f}' if current_rsi is not None else '-'} "
                    f"| {signal_text} | {_md(blockers)} |"
                )
        else:
            lines.append("| 감시 종목 없음 | - | - | - | - | - |")
    drawdown = context.get("account_drawdown_pct")
    daily_return = context.get("daily_return_pct")
    cumulative_return = context.get("cumulative_return_pct")
    twr_return = context.get("twr_return_pct")
    lines += [
        "", "## 계좌 안전장치", "",
        '<table width="100%">',
        '<thead><tr>',
        '<th width="14%">신규 매수</th><th width="24%">계좌 고점 대비 낙폭</th>',
        f'<th width="{RETURN_COLUMN_WIDTH}" nowrap align="center">오늘 수익률</th>',
        f'<th width="{RETURN_COLUMN_WIDTH}" nowrap align="center">누적 수익률</th>',
        f'<th width="{RETURN_COLUMN_WIDTH}" nowrap align="center">전략 수익률 (TWR)</th>',
        '<th width="14%">주문 배율</th>',
        '<th width="14%">남은 슬롯</th>',
        '</tr></thead><tbody><tr>',
        f'<td>{"가능" if context.get("entry_allowed", False) else "중단"}</td>',
        f'<td align="right">{f"{drawdown:.2f}%" if drawdown is not None else "-"}</td>',
        dashboard_return_cell(daily_return, "daily-return", context),
        dashboard_return_cell(cumulative_return, "cumulative-return", context),
        dashboard_return_cell(twr_return, "twr-return", context),
        f'<td align="right">{float(context.get("entry_size_multiplier", 1.0))*100:.0f}%</td>',
        f'<td align="right">{max(0, MAX_POSITIONS-len(positions))}</td>',
        '</tr></tbody></table>',
    ]
    lines += [
        "", "## 전체 성과", "",
        '<table width="100%">',
        '<thead><tr>',
        '<th>청산 거래</th><th>승률</th>',
        f'<th width="{RETURN_COLUMN_WIDTH}" nowrap align="center">거래가중 순수익률</th>',
        f'<th width="{RETURN_COLUMN_WIDTH}" nowrap align="center">평균 이익률</th>',
        f'<th width="{RETURN_COLUMN_WIDTH}" nowrap align="center">평균 손실률</th>',
        '<th>Profit Factor</th><th>최대 연속 손실</th><th>거래가중 비용률</th>',
        '</tr></thead><tbody><tr>',
        f'<td align="center">{len(all_returns)}</td>',
        f'<td align="center">{f"{all_win_rate:.1f}%" if all_win_rate is not None else "-"}</td>',
        dashboard_return_cell(aggregate_net_return, 'aggregate-return', context),
        dashboard_return_cell(average_win, 'average-win', context),
        dashboard_return_cell(average_loss, 'average-loss', context),
        f'<td align="center">{f"{profit_factor:.2f}" if profit_factor is not None else "-"}</td>',
        f'<td align="center">{max_loss_streak}회</td>',
        f'<td align="center">{f"{aggregate_cost_rate:.2f}%" if aggregate_cost_rate is not None else "-"}</td>',
        '</tr></tbody></table>',
        "", "## 청산 전체 기록", "",
        '<table width="100%">',
        '<thead><tr>',
        '<th>진입일</th><th>청산일</th><th>종목</th><th>전략</th><th>보유 기간</th>',
        f'<th width="{RETURN_COLUMN_WIDTH}" nowrap align="center">순수익률</th>',
        '<th>청산 사유</th>',
        '</tr></thead><tbody>',
    ]
    recent_sells = sorted(
        all_sells, key=lambda trade: str(trade.get("timestamp", "")), reverse=True
    )
    all_trades = context.get("all_trades", [])
    for index, trade in enumerate(recent_sells):
        exit_timestamp = str(trade.get("timestamp", ""))
        entry_date = trade.get("entry_date")
        if not entry_date:
            matching_buys = [
                item for item in all_trades
                if item.get("type") == "BUY"
                and item.get("code") == trade.get("code")
                and str(item.get("timestamp", "")) <= exit_timestamp
            ]
            if matching_buys:
                entry_date = str(matching_buys[-1].get("timestamp", ""))[:10]
        holding_days = trade.get("days_held")
        lines.extend([
            '<tr>',
            f'<td>{html.escape(str(entry_date or "-"))}</td>',
            f'<td>{html.escape(exit_timestamp[:10] or "-")}</td>',
            f'<td>{html.escape(str(trade.get("name") or trade.get("code") or "-"))}</td>',
            f'<td>{html.escape(str(trade.get("strategy") or "-"))}</td>',
            f'<td align="center">{f"{int(holding_days)}일" if holding_days is not None else "-"}</td>',
            dashboard_return_cell(
                trade.get('net_return_pct'), f'closed-{index}', context
            ),
            f'<td>{html.escape(str(trade.get("reason") or "-"))}</td>',
            '</tr>',
        ])
    if not recent_sells:
        lines.append('<tr><td colspan="7" align="center">청산 기록 없음</td></tr>')
    lines.append('</tbody></table>')
    lines = order_dashboard_sections(lines, [
        "현재 상태",
        "전략별 현재 판단",
        "현재 보유 포지션",
        "계좌 안전장치",
        "전체 성과",
        "청산 전체 기록",
        "감시 종목 전체",
    ])
    lines += ["", "> 계좌 잔액·원화 손익 등 민감한 계좌정보는 공개하지 않습니다.", ""]
    lines += DASHBOARD_FOOTER_LINES
    return "\n".join(lines)

def build_dashboard_state(status, detail, positions, realtime, render_context, now=None):
    """공개 dashboard_state.json 스냅샷을 만든다(금액은 담지 않고 퍼센트만 둔다)."""
    now = now or datetime.datetime.now()
    realtime_ok, realtime_error = realtime.is_healthy()
    state_positions = []
    for name, pos in positions.items():
        quote = realtime.quote(pos.get("code", ""), DISPLAY_QUOTE_MAX_AGE_SECONDS)
        current_price = float(
            quote.get("price", 0) if quote else pos.get("last_price", 0)
        ) or None
        buy_price = float(pos.get("buy_price", 0))
        state_positions.append({
            "name": name,
            "code": pos.get("code"),
            "shares": int(pos.get("shares", 0)),
            "buy_price": buy_price,
            "current_price": current_price,
            "return_pct": round((current_price - buy_price) / buy_price * 100, 2)
            if current_price and buy_price else None,
            "trailing_active": bool(pos.get("trailing_active", False)),
            "days_held": int(pos.get("days_held", 0)),
        })
    return {
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        "status": status,
        "detail": detail,
        "is_market_open": CURRENT_MARKET_OPEN,
        "position_count": len(positions),
        "positions": state_positions,
        "websocket_connected": realtime_ok,
        "websocket_error": realtime_error,
        "last_quote_at": render_context.get("last_quote_at"),
        "last_account_sync": render_context.get("last_account_sync"),
        "last_loop_at": render_context.get("last_loop_at"),
        "running_commit": render_context.get("commit_hash"),
        "entry_allowed": bool(render_context.get("entry_allowed", False)),
        "stock_entry_block_reason": render_context.get("stock_entry_block_reason"),
        "parking": render_context.get("parking"),
        "core": render_context.get("core"),
        "account_drawdown_pct": render_context.get("account_drawdown_pct"),
        "daily_return_pct": render_context.get("daily_return_pct"),
        "cumulative_return_pct": render_context.get("cumulative_return_pct"),
        "twr_return_pct": render_context.get("twr_return_pct"),
        "entry_size_multiplier": render_context.get("entry_size_multiplier", 1.0),
        "last_external_flow": render_context.get("last_external_flow"),
        "universe_date": render_context.get("universe_date"),
        "universe_source": render_context.get("universe_source"),
        "universe_count": render_context.get("universe_count", 0),
        "universe_symbols": render_context.get("universe_symbols", []),
        "tradeability": render_context.get("tradeability", []),
        "market_favorable": bool(render_context.get("market_favorable", False)),
        "market_regimes": render_context.get("market_regimes", {}),
        "signal_statuses": render_context.get("signal_statuses", []),
        "signal_diagnostics": render_context.get("signal_diagnostics", []),
        "raw_signal_count": render_context.get("raw_signal_count", 0),
        "confirming_signal_count": render_context.get("confirming_signal_count", 0),
        "confirmed_signal_count": render_context.get("confirmed_signal_count", 0),
        "mim_state": render_context.get("mim_state", {}),
        "etf_rotation": render_context.get("etf_rotation", {}),
        "sector_etf": render_context.get("sector_etf", {}),
    }

REPO_URL = "https://github.com/rundevelrun/quant-sniper"
BANNER_FILE = os.path.join(BASE_DIR, "assets", "banner.svg")
_NAV_SEP = "&nbsp;&nbsp;&nbsp;·&nbsp;&nbsp;&nbsp;"
DASHBOARD_HEADER_LINES = [
    '<a name="dashboard-top"></a>',
    "",
    '<p align="center"><img src="assets/banner.svg" alt="Quant Sniper" width="100%"></p>',
    "",
    '<p align="center"><b>' + _NAV_SEP.join([
        f'<a href="{REPO_URL}">← 프로젝트 README</a>',
        f'<a href="{REPO_URL}#한눈에-보기">전략 한눈에 보기</a>',
        f'<a href="{REPO_URL}#백테스트-근거-2017-01--2026-11">백테스트</a>',
        f'<a href="{REPO_URL}#운영-시간표">운영 시간표</a>',
    ]) + "</b></p>",
    "",
    "",
]
DASHBOARD_FOOTER_LINES = [
    "---",
    "",
    '<p align="center"><a href="#dashboard-top">↑ 맨 위로</a>' + _NAV_SEP
    + f'<a href="{REPO_URL}">← 프로젝트 README</a></p>',
    "",
]

def sync_dashboard_to_branch(status, detail, positions, realtime, context=None):
    """대시보드 파일만 부모 없는 단일 커밋으로 origin/dashboard에 교체한다."""
    if not AUTO_GIT_PUSH:
        return False
    events = load_json(DASHBOARD_EVENTS_FILE, [])
    render_context = dict(context or {})
    render_context["all_trades"] = load_json(TRADES_FILE, [])
    render_context["svg_returns"] = True
    now = datetime.datetime.now()
    dashboard_state = build_dashboard_state(
        status, detail, positions, realtime, render_context, now
    )
    save_json(DASHBOARD_STATE_FILE, dashboard_state)
    dashboard = render_dashboard(
        status, detail, positions, realtime, events, context=render_context
    )
    temp_path = f"{DASHBOARD_FILE}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        handle.write(dashboard)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, DASHBOARD_FILE)
    try:
        dashboard_blob = subprocess.run(
            ["git", "hash-object", "-w", DASHBOARD_FILE], cwd=BASE_DIR,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        events_blob = subprocess.run(
            ["git", "hash-object", "-w", "dashboard_events.json"], cwd=BASE_DIR,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        state_blob = subprocess.run(
            ["git", "hash-object", "-w", DASHBOARD_STATE_FILE], cwd=BASE_DIR,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        asset_tree = None
        if os.path.exists(BANNER_FILE):
            banner_blob = subprocess.run(
                ["git", "hash-object", "-w", BANNER_FILE], cwd=BASE_DIR,
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            asset_tree = subprocess.run(
                ["git", "mktree"], cwd=BASE_DIR,
                input=f"100644 blob {banner_blob}\tbanner.svg\n",
                capture_output=True, text=True, check=True,
            ).stdout.strip()
        svg_entries = []
        for asset_name, svg in sorted(render_context.get("svg_assets", {}).items()):
            svg_blob = subprocess.run(
                ["git", "hash-object", "-w", "--stdin"], cwd=BASE_DIR,
                input=svg, capture_output=True, text=True, check=True,
            ).stdout.strip()
            svg_entries.append(f"100644 blob {svg_blob}\t{asset_name}.svg\n")
        svg_tree = None
        if svg_entries:
            svg_tree = subprocess.run(
                ["git", "mktree"], cwd=BASE_DIR, input="".join(svg_entries),
                capture_output=True, text=True, check=True,
            ).stdout.strip()
        tree_input = (
            f"100644 blob {dashboard_blob}\tREADME.md\n"
            f"100644 blob {events_blob}\tdashboard_events.json\n"
            f"100644 blob {state_blob}\tdashboard_state.json\n"
        )
        if svg_tree:
            tree_input += f"040000 tree {svg_tree}\tsvg\n"
        if asset_tree:
            tree_input = f"040000 tree {asset_tree}\tassets\n" + tree_input
        tree = subprocess.run(
            ["git", "mktree"], cwd=BASE_DIR, input=tree_input,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        commit = subprocess.run(
            ["git", "commit-tree", tree, "-m",
             "dashboard: current state"],
            cwd=BASE_DIR, capture_output=True, text=True, check=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "push", "-f", "origin", f"{commit}:refs/heads/dashboard"],
            cwd=BASE_DIR, capture_output=True, text=True, check=True,
        )
        print("[DASHBOARD] origin/dashboard 갱신 완료")
        return True
    except Exception as exc:
        print(f"[DASHBOARD WARN] 갱신 실패: {exc}")
        return False

def confirm_order_fill(api, order_id, code, expected_qty,
                       timeout=ORDER_CONFIRM_TIMEOUT_SECONDS):
    deadline = time.time() + timeout
    last_result = None
    while time.time() < deadline:
        result = api.get_order_execution(order_id, code)
        last_result = result
        if result.get("ok") and result.get("filled_qty", 0) >= expected_qty:
            return result
        time.sleep(1.1)
    return last_result or {"ok": False, "error": "체결 확인 시간 초과"}

def trading_days_held(pos, today_str):
    """실행 중 관측된 거래일을 날짜당 한 번만 누적한다."""
    if pos.get("last_held_date") != today_str:
        pos["days_held"] = pos.get("days_held", 0) + 1
        pos["last_held_date"] = today_str
    return pos.get("days_held", 0)

def build_operational_status(trading_halted, risk_reasons, position_count):
    if trading_halted:
        detail = "; ".join(dict.fromkeys(risk_reasons)) or "안전장치에 의해 신규 주문 중단"
        return "halted", detail
    return "running", f"정규 감시 중 (보유: {position_count}개)"

def confirm_persistent_signals(signals, confirmations, required=3):
    """연속 루프에서 동일하게 유지된 신호만 반환한다."""
    current_keys = {(sig["name"], sig["strategy"]) for sig in signals}
    confirmations = {
        key: count for key, count in confirmations.items() if key in current_keys
    }
    for key in current_keys:
        confirmations[key] = confirmations.get(key, 0) + 1
    confirmed = [
        sig for sig in signals
        if confirmations.get((sig["name"], sig["strategy"]), 0) >= required
    ]
    return confirmed, confirmations

def update_position_stop(pos, current_price):
    """현재가로 진입 후 고점을 갱신하고, 절대로 낮아지지 않는 청산선을 계산한다."""
    buy_price = float(pos["buy_price"])
    stop_loss_pct = float(pos.get("stop_loss_pct", STOP_LOSS_PCT))
    highest_price = max(float(pos.get("highest_price", buy_price)), float(current_price))
    pos["highest_price"] = highest_price
    stop_price = buy_price * (1 - stop_loss_pct)

    is_trend_etf = pos.get("strategy") in {
        ETF_ROTATION_STRATEGY, SECTOR_ETF_STRATEGY
    }
    if is_trend_etf:
        # 3/6개월 추세 신호의 보유 기간에 맞춰 단계형 보호·고점 추적을 쓰지 않는다.
        # 청산은 국면 전환과 재해 손절로 하되, 과거 버전에서 이미 올라간
        # 보호선은 낮추지 않는다.
        locked_stop = float(pos.get("trailing_stop_price") or 0)
        if locked_stop > stop_price:
            return locked_stop, "ETF 기존 보호선"
        return stop_price, f"ETF 재해 손절 (-{stop_loss_pct*100:.1f}%)"

    if highest_price >= buy_price * (1 + TAKE_PROFIT_PCT):
        gap = float(pos.get(
            "trailing_gap_pct",
            max(0.02, min(0.04, stop_loss_pct / 1.5)),
        ))
        pos["trailing_active"] = True
        stop_price = max(float(pos.get("trailing_stop_price", 0)), highest_price * (1 - gap))
        pos["trailing_stop_price"] = stop_price
        reason = "고점 추적 트레일링 스탑"
    elif highest_price >= buy_price * (1 + TRAILING_STOP_TRIGGER):
        stop_price = max(float(pos.get("trailing_stop_price", 0)),
                         buy_price * (1 + TRAILING_STOP_LOCK))
        pos["trailing_stop_price"] = stop_price
        reason = "수익 보호 스탑"
    else:
        reason = f"동적 손절 (-{stop_loss_pct*100:.1f}% ATR 보정)"
    return stop_price, reason

def restore_position_last_prices(api, positions, today_str):
    """재기동 후 실시간 캐시가 없으면 키움 일봉의 최근 종가를 복원한다."""
    changed = False
    for pos in positions.values():
        if float(pos.get("last_price") or 0) > 0:
            continue
        candles = api.get_daily_candles(pos.get("code", ""), 2)
        if not candles:
            continue
        latest = candles[-1]
        close = float(latest.get("close") or 0)
        if close <= 0:
            continue
        pos["last_price"] = close
        pos["last_price_at"] = latest.get("date") or today_str.replace("-", "")
        changed = True
    return changed

def get_kiwoom_candles(api, realtime, cache, code, now, count=70):
    """일봉은 일 1회 REST로 받고 당일 봉만 키움 실시간 체결로 갱신한다."""
    date_key = now.strftime("%Y%m%d")
    cached = cache.get(code)
    if (
        not cached or cached["date"] != date_key
        or int(cached.get("requested_count", len(cached.get("candles", [])))) < count
    ):
        candles = api.get_daily_candles(code, count)
        if not candles:
            return []
        cache[code] = {
            "date": date_key, "candles": candles, "requested_count": count,
        }
    candles = [dict(item) for item in cache[code]["candles"]]
    quote = realtime.quote(code)
    if quote:
        live_candle = {
            "date": date_key, "open": quote["open"], "high": quote["high"],
            "low": quote["low"], "close": quote["price"], "vol": quote["volume"],
        }
        if candles and candles[-1].get("date") == date_key:
            candles[-1].update(live_candle)
        else:
            candles.append(live_candle)
    return candles[-count:]

def account_drawdown_controls(current_assets, peak_assets):
    """계좌 고점을 갱신하고 낙폭에 따른 신규 주문 배율과 중단 여부를 반환한다."""
    current_assets = max(float(current_assets or 0), 0)
    peak_assets = max(float(peak_assets or 0), current_assets)
    drawdown = (peak_assets - current_assets) / peak_assets if peak_assets else 0.0
    halted = drawdown >= EQUITY_HALT_DRAWDOWN_PCT
    size_multiplier = 0.5 if drawdown >= EQUITY_SLOWDOWN_DRAWDOWN_PCT else 1.0
    return peak_assets, drawdown, size_multiplier, halted

def account_snapshot_baseline(account):
    """입출금 추정에 필요한 비민감 계좌 평가 스냅샷을 만든다."""
    return {
        "total_assets": float(account.get("total_assets") or 0),
        "positions": {
            code: {
                "shares": int(pos.get("shares", 0)),
                "current_price": float(pos.get("current_price") or 0),
            }
            for code, pos in account.get("positions", {}).items()
        },
    }

DISTRIBUTION_KEYWORDS = ("배당", "분배", "이자", "이용료")

def holdings_changed(previous_snapshot, account):
    """직전 스냅샷과 보유 수량이 다르면 True(이번 회차에 매매가 있었음)."""
    if not previous_snapshot:
        return False
    prev = {c: int(p.get("shares", 0)) for c, p in previous_snapshot.get("positions", {}).items()}
    cur = {c: int(p.get("shares", 0)) for c, p in account.get("positions", {}).items()}
    return prev != cur

def resolve_external_flow(api, risk_state, today_str, estimate, current_assets, traded=False):
    """잔고 추정 입출금을 증권사 입출금 내역으로 확인해 분배금·이자를 제외한다.

    - 내역 조회 실패 시 기존 추정치를 그대로 쓴다(이전 동작).
    - 같은 내역을 두 번 반영하지 않도록 처리한 내역 번호를 날짜별로 기억한다.
    - 새 내역이 없는데 추정치가 계좌의 1%를 넘으면 추정치를 쓴다(반영 지연 대비).
    """
    # 매매가 있었던 회차는 잔고 추정이 0으로 나오므로 입출금 내역을 직접 확인한다.
    if not estimate and not traded:
        return 0.0
    flows = api.get_cash_flows(today_str) if hasattr(api, "get_cash_flows") else None
    if flows is None:
        return estimate
    seen_state = risk_state.get("seen_cash_flows") or {}
    seen = set(seen_state.get("ids", [])) if seen_state.get("date") == today_str else set()
    new = [f for f in flows if f["id"] not in seen]
    external = sum(
        f["amount"] for f in new
        if not any(word in f["memo"] for word in DISTRIBUTION_KEYWORDS)
    )
    if not new and not traded and abs(estimate) > 0.01 * float(current_assets or 0):
        return estimate
    risk_state["seen_cash_flows"] = {
        "date": today_str, "ids": sorted(seen | {f["id"] for f in new}),
    }
    return float(external)

def estimate_external_cash_flow(previous_snapshot, account):
    """보유수량이 같은 두 계좌 스냅샷에서 가격 변동으로 설명되지 않는 입출금을 추정한다."""
    if not previous_snapshot:
        return 0.0
    previous_positions = previous_snapshot.get("positions", {})
    current_positions = account.get("positions", {})
    previous_quantities = {
        code: int(pos.get("shares", 0)) for code, pos in previous_positions.items()
    }
    current_quantities = {
        code: int(pos.get("shares", 0)) for code, pos in current_positions.items()
    }
    # 매수·매도 직후에는 현금 이동과 외부 입출금을 안전하게 구분할 수 없으므로 보정하지 않는다.
    if previous_quantities != current_quantities:
        return 0.0
    previous_assets = float(previous_snapshot.get("total_assets") or 0)
    current_assets = float(account.get("total_assets") or 0)
    if previous_assets <= 0:
        return 0.0
    market_pnl = sum(
        shares * (
            float(current_positions.get(code, {}).get("current_price") or 0)
            - float(previous_positions.get(code, {}).get("current_price") or 0)
        )
        for code, shares in current_quantities.items()
    )
    unexplained_change = current_assets - previous_assets - market_pnl
    noise_threshold = max(1000.0, previous_assets * 0.005)
    return unexplained_change if abs(unexplained_change) >= noise_threshold else 0.0

CUMULATIVE_RISK_FIELDS = (
    "total_net_capital", "twr_factor", "twr_period_start_assets",
    "cumulative_return_pct", "twr_return_pct",
)

def preserve_cumulative_risk_fields(risk_state):
    """자정 초기화 때 누적 수익률·TWR 기준값이 날아가지 않도록 보존본을 만든다."""
    return {
        key: risk_state.get(key) for key in CUMULATIVE_RISK_FIELDS
        if risk_state.get(key) is not None
    }

def realized_net_profit_total(trades):
    """trades.json에 확정된 청산 손익 합계를 구한다."""
    return sum(
        float(trade.get("net_profit_krw") or 0)
        for trade in trades or []
        if trade.get("type") == "SELL"
    )

def initialize_cumulative_metrics(risk_state, current_assets, realized_net_profit=0.0):
    """누적 원금과 TWR 계수를 최초 1회 초기화한다.

    기존 청산 손익은 이미 계좌에 반영돼 있으므로 원금에서 빼 두어야
    첫날부터의 수익률이 그대로 이어진다.
    """
    current_assets = float(current_assets or 0)
    if current_assets <= 0:
        return False
    changed = False
    total_net_capital = float(risk_state.get("total_net_capital") or 0)
    if total_net_capital <= 0:
        total_net_capital = max(0.0, current_assets - float(realized_net_profit or 0))
        if total_net_capital <= 0:
            total_net_capital = current_assets
        risk_state["total_net_capital"] = total_net_capital
        changed = True
    period_start = float(risk_state.get("twr_period_start_assets") or 0)
    if period_start <= 0:
        risk_state["twr_period_start_assets"] = current_assets
        period_start = current_assets
        changed = True
    if risk_state.get("twr_factor") is None:
        risk_state["twr_factor"] = (
            period_start / total_net_capital if total_net_capital > 0 else 1.0
        )
        changed = True
    return changed

def apply_external_flow_to_metrics(risk_state, current_assets, external_flow):
    """외부 입출금만큼 원금을 보정하고 TWR 구간을 새로 연다."""
    flow = float(external_flow or 0)
    if not flow:
        return False
    current_assets = float(current_assets or 0)
    total_net_capital = float(risk_state.get("total_net_capital") or 0)
    if total_net_capital <= 0:
        total_net_capital = max(0.0, current_assets - flow)
        if total_net_capital <= 0:
            total_net_capital = current_assets
    else:
        total_net_capital = max(0.0, total_net_capital + flow)
    risk_state["total_net_capital"] = total_net_capital
    period_start = float(risk_state.get("twr_period_start_assets") or 0)
    saved_factor = risk_state.get("twr_factor")
    factor = float(saved_factor) if saved_factor is not None else 1.0
    # 입출금 직전까지의 구간 수익률을 확정한 뒤 새 구간을 연다.
    if period_start > 0:
        factor *= (current_assets - flow) / period_start
    risk_state["twr_factor"] = factor
    risk_state["twr_period_start_assets"] = current_assets
    return True

def calculate_cumulative_return_pct(current_assets, total_net_capital):
    """총원금 대비 누적 수익률을 계산한다."""
    total_net_capital = float(total_net_capital or 0)
    if total_net_capital <= 0:
        return None
    return (float(current_assets or 0) - total_net_capital) / total_net_capital * 100

def calculate_twr_return_pct(twr_factor, period_start_assets, current_assets):
    """입출금 영향을 제거한 전략 수익률(TWR)을 계산한다."""
    period_start_assets = float(period_start_assets or 0)
    if period_start_assets <= 0:
        return None
    factor = float(twr_factor) if twr_factor is not None else 1.0
    return (factor * (float(current_assets or 0) / period_start_assets) - 1.0) * 100

def update_cumulative_account_metrics(risk_state, current_assets, realized_net_profit=0.0,
                                      external_flow=0.0):
    """누적 수익률과 TWR을 갱신해 risk_state에 남기고 대시보드용 값을 돌려준다."""
    current_assets = float(current_assets or 0)
    if current_assets <= 0:
        return {}
    # 원금을 이번에 새로 잡았다면 그 기준액이 이미 입출금을 반영한 평가액이므로
    # 같은 호출에서 감지된 입출금을 다시 더하지 않는다.
    had_capital = float(risk_state.get("total_net_capital") or 0) > 0
    initialize_cumulative_metrics(risk_state, current_assets, realized_net_profit)
    if external_flow and had_capital:
        apply_external_flow_to_metrics(risk_state, current_assets, external_flow)
    cumulative = calculate_cumulative_return_pct(
        current_assets, risk_state.get("total_net_capital")
    )
    twr = calculate_twr_return_pct(
        risk_state.get("twr_factor"),
        risk_state.get("twr_period_start_assets"),
        current_assets,
    )
    risk_state["cumulative_return_pct"] = (
        round(cumulative, 4) if cumulative is not None else None
    )
    risk_state["twr_return_pct"] = round(twr, 4) if twr is not None else None
    return {
        "cumulative_return_pct": risk_state["cumulative_return_pct"],
        "twr_return_pct": risk_state["twr_return_pct"],
    }

def restore_daily_account_metrics(api, risk_state, today_str):
    """장 마감 후 오늘 수익률을 저장값에서 읽거나 키움 계좌로 한 번 복원한다."""
    if risk_state.get("date") != today_str:
        return {}, False
    opening_assets = float(risk_state.get("opening_assets") or 0)
    if opening_assets <= 0:
        return {}, False
    saved_return = risk_state.get("daily_return_pct")
    saved_drawdown = risk_state.get("account_drawdown_pct")
    saved_cumulative = risk_state.get("cumulative_return_pct")
    saved_twr = risk_state.get("twr_return_pct")
    if (saved_return is not None and saved_drawdown is not None
            and saved_cumulative is not None and saved_twr is not None):
        return {
            "daily_return_pct": float(saved_return),
            "account_drawdown_pct": float(saved_drawdown),
            "entry_size_multiplier": float(
                risk_state.get("entry_size_multiplier", 1.0)
            ),
            "cumulative_return_pct": float(saved_cumulative),
            "twr_return_pct": float(saved_twr),
        }, False

    account = api.get_account_snapshot()
    if not account.get("ok"):
        return {}, False
    current_assets = float(account.get("total_assets") or opening_assets)
    external_flow = resolve_external_flow(
        api, risk_state, today_str,
        estimate_external_cash_flow(risk_state.get("account_baseline"), account),
        current_assets,
        traded=holdings_changed(risk_state.get("account_baseline"), account),
    )
    if external_flow:
        opening_assets = max(0.0, opening_assets + external_flow)
        flow_core_state = load_json(CORE_STATE_FILE, {})
        if mark_flow_rebalance(flow_core_state, external_flow, current_assets):
            save_json(CORE_STATE_FILE, flow_core_state)
            print(f"[ACCOUNT] 외부 입출금 {external_flow:+,.0f}원: 다음 창에서 코어 리밸런싱")
    peak_assets, drawdown, multiplier, _ = account_drawdown_controls(
        current_assets, float(risk_state.get("peak_assets") or current_assets)
        + external_flow
    )
    daily_return_pct = (
        (current_assets - opening_assets) / opening_assets * 100
        if opening_assets > 0 else 0.0
    )
    cumulative_metrics = update_cumulative_account_metrics(
        risk_state, current_assets,
        realized_net_profit_total(load_json(TRADES_FILE, [])),
        external_flow=external_flow,
    )
    risk_state.update({
        "opening_assets": opening_assets,
        "peak_assets": peak_assets,
        "account_baseline": account_snapshot_baseline(account),
        "daily_return_pct": daily_return_pct,
        "account_drawdown_pct": drawdown * 100,
        "entry_size_multiplier": multiplier,
    })
    return {
        "daily_return_pct": daily_return_pct,
        "account_drawdown_pct": drawdown * 100,
        "entry_size_multiplier": multiplier,
        **cumulative_metrics,
    }, True

def time_stop_reason(pos, current_price):
    """손실·정체 포지션은 7일, 수익 포지션은 14일까지 두고 추적 활성 시 면제한다."""
    if pos.get("trailing_active"):
        return None
    days_held = int(pos.get("days_held", 0))
    buy_price = float(pos.get("buy_price", 0))
    if days_held >= MAX_PROFIT_HOLD_DAYS:
        return f"수익 포지션 연장 타임스탑 ({MAX_PROFIT_HOLD_DAYS}거래일)"
    if days_held >= MAX_HOLD_DAYS and float(current_price) <= buy_price:
        return f"타임스탑 (비수익 상태 {MAX_HOLD_DAYS}거래일)"
    return None

def calculate_entry_shares(total_assets, cash, entry_price, stop_loss_pct,
                           size_multiplier=1.0, risk_pct=None, alloc_pct=None):
    """종목당 투자 한도와 거래당 계획 손실(기본 0.7%)을 모두 지키는 정수 수량을 산출한다."""
    risk_pct = MAX_RISK_PER_TRADE_PCT if risk_pct is None else risk_pct
    alloc_pct = MAX_ALLOC_PER_STOCK if alloc_pct is None else alloc_pct
    if min(total_assets, cash, entry_price, stop_loss_pct, size_multiplier) <= 0:
        return 0
    allocation_cap = total_assets * alloc_pct * size_multiplier
    risk_budget = total_assets * risk_pct * size_multiplier
    risk_per_share = entry_price * stop_loss_pct
    shares_by_allocation = int(allocation_cap // entry_price)
    shares_by_risk = int(risk_budget // risk_per_share)
    shares_by_cash = int(cash // entry_price)
    return max(0, min(shares_by_allocation, shares_by_risk, shares_by_cash))

def entry_cash_after_reserve(total_assets, cash):
    """총자산의 10% 현금 버퍼를 제외하고 신규 주문에 쓸 수 있는 금액."""
    reserve = total_assets * (1.0 - MAX_TOTAL_EXPOSURE_PCT)
    return max(0.0, cash - reserve)


# ---- 대기자금(단기금리 ETF) ----
def parking_shares(parking):
    return max(0, int((parking or {}).get("shares", 0) or 0))

def parking_reference_price(parking, quote=None):
    """실시간가가 없으면 마지막 확인가·평균단가로 대체한다(주문 수량 추정용)."""
    if quote and float(quote.get("price") or 0) > 0:
        return float(quote["price"])
    parking = parking or {}
    return float(parking.get("last_price") or parking.get("avg_price") or 0)

def parking_liquidation_value(parking, price):
    """대기자금을 지금 팔면 받을 보수적 금액."""
    shares = parking_shares(parking)
    if shares <= 0 or price <= 0:
        return 0.0
    return shares * price * (1.0 - PARKING_VALUE_HAIRCUT_PCT)

def account_with_parking_cash(account, parking, price):
    """전략 계산용 계좌: 대기자금 ETF를 현금으로 본다."""
    value = parking_liquidation_value(parking, price)
    if not account.get("ok") or value <= 0:
        return account
    return {**account, "cash": float(account.get("cash") or 0) + value}

def parking_buy_shares(total_assets, cash, price):
    """실제 현금은 계좌의 2%만 남기고 넣을 수 있는 대기자금 ETF 수량.

    10% 현금 버퍼도 대기자금 ETF로 운용한다. 매수 시에는 대기자금을 먼저 팔아
    자금을 마련하므로(대기자금은 현금으로 취급) 버퍼 역할은 그대로 유지된다.
    """
    if not PARKING_ENABLED or price <= 0:
        return 0
    idle = max(0.0, float(cash) - float(total_assets) * PARKING_CASH_RESERVE_PCT)
    if idle < max(PARKING_MIN_KRW, price):
        return 0
    return int(idle // (price * (1.0 + PARKING_VALUE_HAIRCUT_PCT)))

def parking_sell_shares_needed(shortfall, price, held_shares):
    """매수 자금 부족분을 채우는 데 필요한 최소 매도 수량."""
    if shortfall <= 0 or price <= 0 or held_shares <= 0:
        return 0
    needed = math.ceil(shortfall * (1.0 + PARKING_VALUE_HAIRCUT_PCT) / price)
    return min(int(held_shares), max(1, needed))

def apply_parking_fill(parking, side, filled_qty, fill_price, today_str):
    """대기자금 체결을 로컬 장부에 반영한다(평균단가 유지)."""
    parking = dict(parking or {})
    shares = parking_shares(parking)
    if filled_qty <= 0:
        return parking
    if side == "buy":
        avg = float(parking.get("avg_price") or 0)
        new_shares = shares + filled_qty
        parking["avg_price"] = (
            (avg * shares + fill_price * filled_qty) / new_shares
        )
        parking["shares"] = new_shares
    else:
        parking["shares"] = max(0, shares - filled_qty)
        if parking["shares"] == 0:
            parking.pop("avg_price", None)
    parking["code"] = PARKING_ETF_CODE
    parking["last_price"] = fill_price
    parking["last_trade_date"] = today_str
    return parking

def is_parking_window(now=None):
    now = now or datetime.datetime.now()
    start_hour, start_minute = map(int, PARKING_START_TIME.split(":"))
    end_hour, end_minute = map(int, PARKING_END_TIME.split(":"))
    start = now.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
    end = now.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
    return start <= now <= end

def parking_dashboard_info(parking, price, total_assets):
    """공개 대시보드용: 금액·수량 없이 비중과 수익률만."""
    shares = parking_shares(parking)
    if shares <= 0:
        return None
    avg = float(parking.get("avg_price") or 0)
    return {
        "name": PARKING_ETF_NAME,
        "code": PARKING_ETF_CODE,
        "weight_pct": (
            round(shares * price / total_assets * 100, 1)
            if price > 0 and total_assets > 0 else None
        ),
        "return_pct": (
            round((price - avg) / avg * 100, 2) if price > 0 and avg > 0 else None
        ),
    }

def execute_etf_order(api, side, code, name, shares, price_hint, reason):
    """ETF 시장가 주문 후 체결 수량·가격을 확인한다. ETF는 KRX로만 보낸다.

    반환: (체결 수량, 체결가, 오류 메시지 또는 None). 일부만 체결돼도 체결분은 반환한다.
    """
    if side == "buy":
        order = api.send_buy_order(code, name, shares, price_hint, exchange="KRX")
    else:
        order = api.send_sell_order(
            code, name, shares, price_hint, reason, exchange="KRX",
        )
    if order.get("status") == "filled":
        return shares, float(price_hint), None
    if order.get("status") == "accepted":
        fill = confirm_order_fill(api, order.get("order_id"), code, shares)
        filled = int(fill.get("filled_qty", 0) or 0)
        return filled, float(fill.get("fill_price") or price_hint), (
            None if filled >= shares else fill.get("error", "체결 확인 시간 초과")
        )
    return 0, 0.0, order.get("message", "알 수 없는 주문 상태")

def execute_parking_order(api, side, shares, price_hint):
    """대기자금 ETF 주문."""
    return execute_etf_order(
        api, side, PARKING_ETF_CODE, PARKING_ETF_NAME, shares, price_hint,
        "대기자금 회수",
    )

# ---- 코어 자산배분(고정 비중, 월 1회 리밸런싱) ----
def core_code_conflicts():
    """코어 ETF가 다른 전략 종목과 겹치면 잔고 대조가 불가능하므로 겹치는 코드를 반환한다."""
    others = set(ETF_ROTATION_ASSETS) | set(SECTOR_ETF_ASSETS) | {
        PARKING_ETF_CODE, MARKET_PROXY_CODE, KOSDAQ_MARKET_PROXY_CODE,
    }
    return sorted(set(CORE_TARGETS) & others)

def core_holdings(core_state):
    return (core_state or {}).get("holdings", {})

def core_shares(core_state, code):
    return max(0, int(core_holdings(core_state).get(code, {}).get("shares", 0) or 0))

def is_core_rebalance_window(now=None):
    now = now or datetime.datetime.now()
    start_hour, start_minute = map(int, CORE_REBALANCE_START_TIME.split(":"))
    end_hour, end_minute = map(int, CORE_REBALANCE_END_TIME.split(":"))
    start = now.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
    end = now.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
    return start <= now <= end

def core_rebalance_due(core_state, now):
    """이번 달에 아직 비중을 맞추지 않았거나 큰 입출금이 있었으면 True."""
    core_state = core_state or {}
    return (
        core_state.get("last_rebalance_month") != now.strftime("%Y-%m")
        or bool(core_state.get("flow_rebalance_pending"))
    )

def mark_flow_rebalance(core_state, external_flow, current_assets):
    """계좌 대비 큰 입출금이면 다음 리밸런싱 창에서 코어를 다시 맞추도록 표시한다."""
    flow = abs(float(external_flow or 0))
    assets = float(current_assets or 0)
    if not CORE_ALLOCATION_ENABLED or assets <= 0:
        return False
    if flow < assets * CORE_FLOW_REBALANCE_PCT:
        return False
    if core_state.get("flow_rebalance_pending"):
        return False
    core_state["flow_rebalance_pending"] = True
    return True

def core_desired_shares(target_value, price, current_shares, min_trade_value):
    """목표 금액에 맞는 정수 수량. 차이가 작으면 현재 수량을 유지해 잦은 매매를 막는다."""
    if price <= 0:
        return current_shares
    ratio = target_value / price
    if ratio >= 1:
        # 버림을 쓰면 1.999주가 1주가 되어 경계에서 사고팔기를 반복한다. 가장 가까운 수량을 쓴다.
        desired = int(ratio + 0.5)
    else:
        # 1주 미만 구간은 1주 값이 목표의 허용 배수 이내일 때만 1주를 보유한다.
        desired = 1 if target_value > 0 and price <= target_value * CORE_ONE_SHARE_TOLERANCE else 0
    if current_shares > 0 and abs(desired - current_shares) * price < min_trade_value:
        return current_shares
    return desired

def plan_core_rebalance(total_assets, prices, core_state):
    """코어 목표 대비 매도·매수 계획을 만든다. 매도를 먼저 실행하도록 정렬한다.

    반환: [(side, code, name, shares, price)], 가격이 없는 종목이 있으면 None.
    """
    if total_assets <= 0:
        return []
    budget = total_assets * CORE_ALLOCATION_PCT
    min_trade_value = total_assets * CORE_MIN_TRADE_PCT
    sells, buys = [], []
    for code, (name, weight) in CORE_TARGETS.items():
        price = float(prices.get(code) or 0)
        if price <= 0:
            return None
        current = core_shares(core_state, code)
        desired = core_desired_shares(budget * weight, price, current, min_trade_value)
        if desired < current:
            sells.append(("sell", code, name, current - desired, price))
        elif desired > current:
            buys.append(("buy", code, name, desired - current, price))
    # 비중이 큰 자산부터 채운다.
    buys.sort(key=lambda item: item[3] * item[4], reverse=True)
    return sells + buys

def remember_core_last_prices(core_state, prices):
    """보유 코어 종목의 마지막 확인가를 갱신한다. 변경이 있으면 True."""
    changed = False
    for code, item in core_holdings(core_state).items():
        price = float((prices or {}).get(code) or 0)
        if price > 0 and float(item.get("last_price") or 0) != price:
            item["last_price"] = price
            changed = True
    return changed

def apply_core_fill(core_state, side, code, filled_qty, fill_price):
    """코어 체결을 로컬 장부에 반영한다(평균단가 유지)."""
    core_state = dict(core_state or {})
    holdings = {k: dict(v) for k, v in core_holdings(core_state).items()}
    if filled_qty > 0:
        item = holdings.get(code, {})
        shares = max(0, int(item.get("shares", 0) or 0))
        if side == "buy":
            avg = float(item.get("avg_price") or 0)
            item["avg_price"] = (avg * shares + fill_price * filled_qty) / (shares + filled_qty)
            item["shares"] = shares + filled_qty
        else:
            item["shares"] = max(0, shares - filled_qty)
        item["last_price"] = fill_price
        if item["shares"] > 0:
            holdings[code] = item
        else:
            holdings.pop(code, None)
    core_state["holdings"] = holdings
    return core_state

def core_dashboard_info(core_state, prices, total_assets):
    """공개 대시보드용: 금액·수량 없이 목표·현재 비중과 수익률만."""
    rows = []
    for code, (name, weight) in CORE_TARGETS.items():
        item = core_holdings(core_state).get(code, {})
        shares = max(0, int(item.get("shares", 0) or 0))
        price = float(prices.get(code) or item.get("last_price") or 0)
        avg = float(item.get("avg_price") or 0)
        rows.append({
            "name": name,
            "code": code,
            "held": shares > 0,
            "days_held": int(item.get("days_held", 0) or 0) if shares > 0 else None,
            "target_pct": round(CORE_ALLOCATION_PCT * weight * 100, 1),
            "weight_pct": (
                round(shares * price / total_assets * 100, 1)
                if total_assets > 0 and price > 0 else None
            ),
            "return_pct": (
                round((price - avg) / avg * 100, 2)
                if shares > 0 and price > 0 and avg > 0 else None
            ),
        })
    return {
        "enabled": CORE_ALLOCATION_ENABLED,
        "last_rebalance_month": (core_state or {}).get("last_rebalance_month"),
        "assets": rows,
    }

def screening_preferred_max_price(account):
    """당일 허용 상승 후에도 비우호장 50%에서 1주 가능한 전일종가 상한."""
    if not account.get("ok"):
        return None
    total_assets = float(account.get("total_assets") or 0)
    cash = float(account.get("cash") or 0)
    if min(total_assets, cash) <= 0:
        return None
    live_price_cap = min(
        cash,
        total_assets * MAX_ALLOC_PER_STOCK * 0.5,
        total_assets * MAX_RISK_PER_TRADE_PCT * 0.5 / MAX_STOP_LOSS_PCT,
    )
    return live_price_cap / (1.0 + MAX_ENTRY_DAY_RETURN_PCT)

def intraday_surge_max_price(account):
    """15% 전략이 최대 손절폭에서도 1주를 살 수 있는 실시간 가격 상한."""
    if not account.get("ok"):
        return None
    total_assets = float(account.get("total_assets") or 0)
    cash = float(account.get("cash") or 0)
    if min(total_assets, cash) <= 0:
        return None
    return min(
        cash,
        total_assets * INTRADAY_SURGE_MAX_INVESTMENT_PCT,
        total_assets * MAX_RISK_PER_TRADE_PCT
        * INTRADAY_SURGE_SIZE_MULTIPLIER / MAX_STOP_LOSS_PCT,
    )

def apply_intraday_surge_limits(signals, surge_names):
    """당일 발굴 후보의 VBO 신호만 15%·동시 1종목 전략으로 격리한다."""
    limited = []
    for signal in signals:
        signal = dict(signal)
        if (
            signal.get("name") in surge_names
            and signal.get("strategy") in {
                "변동성 돌파 (추세)", "비우호장 강한 VBO (15%)"
            }
        ):
            signal.update({
                "strategy": INTRADAY_SURGE_STRATEGY,
                "size_multiplier": min(
                    float(signal.get("size_multiplier", 1.0)),
                    INTRADAY_SURGE_SIZE_MULTIPLIER,
                ),
                "max_investment_pct": INTRADAY_SURGE_MAX_INVESTMENT_PCT,
            })
        limited.append(signal)
    return limited

def sector_limit_reached(candidate_sector, positions, universe_metadata,
                         max_per_sector=MAX_POSITIONS_PER_SECTOR):
    """미분류를 제외하고 동일 업종 보유 수가 상한에 도달했는지 확인한다."""
    if not candidate_sector or candidate_sector == "미분류":
        return False
    same_sector = 0
    for name, pos in positions.items():
        sector = pos.get("sector") or universe_metadata.get(name, {}).get("sector")
        if sector == candidate_sector:
            same_sector += 1
    return same_sector >= max_per_sector

def build_tradeability(active_universe, universe_metadata, candles_dict,
                       total_assets, cash, size_multiplier=1.0,
                       market_regimes=None):
    """현재가·ATR·계좌·시장 국면 한도로 실제 주문 가능 수량을 산출한다."""
    results = []
    for name, code in active_universe.items():
        meta = universe_metadata.get(name, {})
        market = meta.get("market", "미분류")
        regime_multiplier = (
            1.0 if market_regimes is None or market_regimes.get(market, True)
            else 0.5
        )
        candles = candles_dict.get(name, [])
        price = float(candles[-1]["close"]) if candles else 0.0
        if price > 0:
            atr = calculate_atr(candles)
            stop_pct = max(
                MIN_STOP_LOSS_PCT,
                min(MAX_STOP_LOSS_PCT, atr / price * ATR_STOP_MULTIPLIER),
            )
            favorable_shares = calculate_entry_shares(
                total_assets, cash, price, stop_pct, size_multiplier
            )
            unfavorable_shares = calculate_entry_shares(
                total_assets, cash, price, stop_pct, size_multiplier * 0.5
            )
            shares = favorable_shares if regime_multiplier == 1.0 else unfavorable_shares
            if regime_multiplier < 1:
                reason = (
                    "비우호장 50% 한도에서 가능" if shares > 0
                    else "비우호장 50% 투자/위험 한도에서 1주 초과"
                )
            else:
                reason = "1주 이상 가능" if shares > 0 else "1주가 투자/위험/현금 한도 초과"
        else:
            stop_pct = None
            shares = 0
            favorable_shares = 0
            unfavorable_shares = 0
            reason = "가격 데이터 없음"
        results.append({
            "name": name, "code": code,
            "market": market,
            "sector": meta.get("sector", "미분류"),
            "price": price or None,
            "stop_loss_pct": stop_pct,
            "max_shares": shares,
            "favorable_max_shares": favorable_shares,
            "unfavorable_max_shares": unfavorable_shares,
            "market_size_multiplier": regime_multiplier,
            "eligible": shares > 0,
            "reason": reason,
        })
    return sorted(
        results,
        key=lambda item: (
            item.get("price") is None,
            float(item.get("price") or 0),
            item.get("name", ""),
        ),
    )


def build_etf_tradeability(assets, strategy, allocation_pct, stop_loss_pct,
                           total_assets, cash, quotes,
                           size_multiplier=1.0,
                           allow_one_share_over_allocation=False,
                           risk_pct=None):
    """ETF 전략의 실제 주문식과 동일한 한도로 매수 가능 수량을 계산한다."""
    available_cash = entry_cash_after_reserve(total_assets, cash)
    results = []
    for code, name in assets.items():
        quote = quotes.get(code) or {}
        price = float(quote.get("price") or 0)
        shares = calculate_entry_shares(
            total_assets, available_cash, price, stop_loss_pct, size_multiplier,
            risk_pct, max(allocation_pct, MAX_ALLOC_PER_STOCK),
        )
        allocation_shares = (
            int(total_assets * allocation_pct // price) if price > 0 else 0
        )
        risk_cash_shares = shares
        if allow_one_share_over_allocation and shares >= 1:
            allocation_shares = max(1, allocation_shares)
        shares = min(shares, allocation_shares)
        if price <= 0:
            reason = "키움 실시간 가격 없음"
        elif available_cash < price:
            reason = "현금 버퍼 제외 후 1주 매수 불가"
        elif allocation_shares < 1:
            reason = f"전략 배정 한도 {allocation_pct * 100:.0f}%에서 1주 매수 불가"
        elif shares < 1:
            reason = "거래당 위험 한도에서 1주 매수 불가"
        else:
            reason = f"최대 {shares}주 가능"
        results.append({
            "strategy": strategy,
            "name": name,
            "code": code,
            "price": price or None,
            "allocation_pct": allocation_pct * 100,
            "one_share_exception": bool(
                allow_one_share_over_allocation
                and risk_cash_shares >= 1
                and total_assets * allocation_pct < price
            ),
            "max_shares": shares,
            "eligible": shares > 0,
            "reason": reason,
        })
    return results

def check_git_permission():
    """봇 기동 시 깃 푸시 권한 사전 점검"""
    if not AUTO_GIT_PUSH:
        print("[GIT] AUTO_GIT_PUSH=false: GitHub 연동이 비활성화되었습니다.")
        return False
    repo_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        res = subprocess.run(["git", "push", "--dry-run"], cwd=repo_dir, capture_output=True, text=True)
        if res.returncode == 0:
            print("[GIT] ✅ 깃허브 푸시 권한 정상 확인")
            return True
        else:
            print(f"[GIT WARN] ⚠️ 깃허브 푸시 권한 없음: {res.stderr.strip() or '인증 필요'}")
            print("          (거래는 로컬에 기록되나, 깃 인증 전까지 비서 알림이 보류됩니다.)")
            return False
    except Exception as e:
        print(f"[GIT WARN] git 실행 오류: {e}")
        return False

def git_commit_and_push(commit_msg):
    """체결 내역 발생 시 깃허브 자동 푸시"""
    repo_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        subprocess.run(["git", "add", "trades.json", "positions.json"], cwd=repo_dir,
                       capture_output=True, text=True, check=True)
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=repo_dir,
                       capture_output=True, text=True, check=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=repo_dir,
                       capture_output=True, text=True, check=True)
        print(f"[GIT] 자동 커밋 및 푸시 완료: {commit_msg}")
        return True
    except Exception as e:
        print(f"[GIT ERR] 체결 내역 push 실패: {e}")
        return False

def is_market_open(now=None, live_status=False):
    """시간과 당일 실시간 상태가 모두 확인된 경우에만 장이 열린 것으로 본다."""
    now = now or datetime.datetime.now()
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if not market_open <= now <= market_close:
        return False
    return bool(live_status)

def is_entry_window(now=None):
    now = now or datetime.datetime.now()
    start_hour, start_minute = map(int, ENTRY_START_TIME.split(":"))
    end_hour, end_minute = map(int, ENTRY_END_TIME.split(":"))
    start = now.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
    end = now.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
    return start <= now <= end

def is_stock_entry_window(now=None):
    """개별주 일봉 신호는 종가에 가까운 장 막판에만 진입한다."""
    now = now or datetime.datetime.now()
    start_hour, start_minute = map(int, STOCK_ENTRY_START_TIME.split(":"))
    end_hour, end_minute = map(int, STOCK_ENTRY_END_TIME.split(":"))
    start = now.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
    end = now.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
    return start <= now <= end

def stock_entry_block_reason(now, rotation_target_code, positions):
    """개별주 신규 매수를 막아야 하면 사유를, 허용되면 None을 반환한다."""
    if not STOCK_STRATEGIES_ENABLED:
        return "개별주 전략 비활성화(보유분만 청산 감시)"
    if BLOCK_STOCK_ENTRIES_WHEN_INVERSE and ETF_ROTATION_ENABLED and (
        rotation_target_code == ETF_ROTATION_INVERSE_CODE
        or any(
            pos.get("code") == ETF_ROTATION_INVERSE_CODE
            for pos in positions.values()
        )
    ):
        return "인버스 ETF 국면: 개별주 신규 매수 차단"
    if not is_stock_entry_window(now):
        return (
            f"개별주 진입 시간({STOCK_ENTRY_START_TIME}~{STOCK_ENTRY_END_TIME}) 외"
        )
    return None

def is_mim_entry_window(now=None):
    now = now or datetime.datetime.now()
    start_hour, start_minute = map(int, MIM_ENTRY_START_TIME.split(":"))
    end_hour, end_minute = map(int, MIM_ENTRY_END_TIME.split(":"))
    start = now.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
    end = now.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
    return start <= now <= end

def update_mim_state(state, now, quote, previous_close):
    """연구 구간의 경계 가격을 90초 안에만 수집해 재기동 추격을 막는다."""
    today = now.strftime("%Y-%m-%d")
    if state.get("date") != today:
        state = {"date": today, "snapshots": {}, "entry_attempted": False}
    snapshots = state.setdefault("snapshots", {})
    if quote and previous_close and previous_close > 0:
        snapshots.setdefault("previous_close", float(previous_close))
        snapshots.setdefault("open", float(quote.get("open") or 0))
        for key, hour, minute in (
            ("0930", 9, 30), ("1000", 10, 0),
            ("1430", 14, 30), ("1500", 15, 0),
        ):
            boundary = now.replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )
            if boundary <= now <= boundary + datetime.timedelta(seconds=90):
                snapshots.setdefault(key, float(quote.get("price") or 0))
    return state

def mim_long_signal(state):
    """Overnight·두 번째 30분·마감 직전 30분이 모두 상승일 때만 롱."""
    values = state.get("snapshots", {})
    required = ("previous_close", "open", "0930", "1000", "1430", "1500")
    if any(float(values.get(key) or 0) <= 0 for key in required):
        return False
    return (
        values["open"] > values["previous_close"]
        and values["1000"] > values["0930"]
        and values["1500"] > values["1430"]
    )

def etf_rotation_regime(candles, now=None):
    """전일 확정 종가의 60일선·기울기로 long/cash/inverse를 결정한다."""
    now = now or datetime.datetime.now()
    today = now.strftime("%Y%m%d")
    completed = [
        candle for candle in candles
        if str(candle.get("date", "")).replace("-", "") != today
        and float(candle.get("close") or 0) > 0
    ]
    needed = ETF_ROTATION_MA_PERIOD + ETF_ROTATION_SLOPE_DAYS
    if len(completed) < needed:
        return "cash", {"reason": f"확정 일봉 {needed}개 미만"}
    closes = [float(candle["close"]) for candle in completed]
    close = closes[-1]
    ma_now = sum(closes[-ETF_ROTATION_MA_PERIOD:]) / ETF_ROTATION_MA_PERIOD
    previous_end = -ETF_ROTATION_SLOPE_DAYS
    ma_previous = (
        sum(closes[previous_end-ETF_ROTATION_MA_PERIOD:previous_end])
        / ETF_ROTATION_MA_PERIOD
    )
    upper = ma_now * (1 + ETF_ROTATION_BAND_PCT)
    lower = ma_now * (1 - ETF_ROTATION_BAND_PCT)
    if close > upper and ma_now > ma_previous:
        regime = "long"
    elif close < lower and ma_now < ma_previous:
        regime = "inverse"
    else:
        regime = "cash"
    return regime, {
        "previous_close": close,
        "ma": ma_now,
        "ma_5d_ago": ma_previous,
        "upper": upper,
        "lower": lower,
        "reason": "60일선 ±1%와 5거래일 기울기",
    }


def etf_entry_gap_status(previous_close, current_price,
                         max_adverse_gap_pct=ETF_MAX_ADVERSE_ENTRY_GAP_PCT):
    """전일 종가 대비 ETF 가격이 허용 역방향 갭 안으로 회복했는지 판단한다."""
    previous_close = float(previous_close or 0)
    current_price = float(current_price or 0)
    if previous_close <= 0 or current_price <= 0:
        return False, None
    gap_pct = current_price / previous_close - 1
    return gap_pct + 1e-12 >= -max_adverse_gap_pct, gap_pct

def multi_asset_trend_selection(candles_by_code, held_code=None, now=None):
    """전일 확정 일봉만으로 상승 추세 자산 중 모멘텀이 가장 강한 ETF를 고른다."""
    now = now or datetime.datetime.now()
    today = now.strftime("%Y%m%d")
    needed = max(
        ETF_ROTATION_MA_PERIOD + ETF_ROTATION_SLOPE_DAYS,
        ETF_ROTATION_LONG_MOMENTUM_DAYS + 1,
    )
    rankings = []
    asset_details = {}
    for code, candles in candles_by_code.items():
        completed = [
            candle for candle in candles
            if str(candle.get("date", "")).replace("-", "") != today
            and float(candle.get("close") or 0) > 0
        ]
        if len(completed) < needed:
            asset_details[code] = {
                "name": ETF_ROTATION_ASSETS.get(code, code),
                "eligible": False,
                "reason": f"확정 일봉 {needed}개 미만",
            }
            continue
        closes = [float(candle["close"]) for candle in completed]
        close = closes[-1]
        ma_now = sum(closes[-ETF_ROTATION_MA_PERIOD:]) / ETF_ROTATION_MA_PERIOD
        previous_end = -ETF_ROTATION_SLOPE_DAYS
        ma_previous = (
            sum(closes[previous_end-ETF_ROTATION_MA_PERIOD:previous_end])
            / ETF_ROTATION_MA_PERIOD
        )
        short_return = close / closes[-ETF_ROTATION_SHORT_MOMENTUM_DAYS - 1] - 1
        long_return = close / closes[-ETF_ROTATION_LONG_MOMENTUM_DAYS - 1] - 1
        score = short_return * 0.4 + long_return * 0.6
        eligible = (
            close > ma_now * (1 + ETF_ROTATION_BAND_PCT)
            and ma_now > ma_previous
            and score > 0
        )
        detail = {
            "name": ETF_ROTATION_ASSETS.get(code, code),
            "previous_close": close,
            "ma": ma_now,
            "ma_5d_ago": ma_previous,
            "short_return_pct": round(short_return * 100, 2),
            "long_return_pct": round(long_return * 100, 2),
            "score": score,
            "score_pct": round(score * 100, 2),
            "eligible": eligible,
            "reason": "60일선 +1%·상승 기울기·3/6개월 모멘텀",
        }
        asset_details[code] = detail
        if eligible:
            rankings.append((score, code))

    rankings.sort(reverse=True)
    if not rankings:
        return "cash", None, {
            "reason": "상승 추세 자산 없음", "assets": asset_details,
        }

    best_score, target_code = rankings[0]
    # 작은 순위 변화로 ETF를 계속 갈아타지 않도록 기존 자산에 2%p 우선권을 준다.
    if held_code and held_code != target_code:
        held_detail = asset_details.get(held_code, {})
        if (
            held_detail.get("eligible")
            and best_score < float(held_detail.get("score", 0))
                + ETF_ROTATION_SWITCH_MARGIN
        ):
            target_code = held_code

    target = asset_details[target_code]
    return "asset", target_code, {
        **target,
        "target_name": target["name"],
        "assets": asset_details,
        "reason": (
            "전일 확정 3/6개월 모멘텀 40/60%·60일선 +1%·"
            f"교체 완충 {ETF_ROTATION_SWITCH_MARGIN*100:.0f}%p"
        ),
    }

def sector_etf_trend_selection(candles_by_code, benchmark_candles,
                               held_code=None, now=None):
    """시장보다 강하고 자체 상승 추세인 섹터 ETF 한 종목을 선택한다."""
    now = now or datetime.datetime.now()
    today = now.strftime("%Y%m%d")
    needed = max(
        SECTOR_ETF_MA_PERIOD + SECTOR_ETF_SLOPE_DAYS,
        SECTOR_ETF_LONG_MOMENTUM_DAYS + 1,
    )
    benchmark_completed = [
        candle for candle in benchmark_candles
        if str(candle.get("date", "")).replace("-", "") != today
        and float(candle.get("close") or 0) > 0
    ]
    if len(benchmark_completed) < SECTOR_ETF_SHORT_MOMENTUM_DAYS + 1:
        return "cash", None, {
            "reason": "시장 비교 일봉 부족", "assets": {},
        }
    benchmark_closes = [float(item["close"]) for item in benchmark_completed]
    benchmark_return = (
        benchmark_closes[-1]
        / benchmark_closes[-SECTOR_ETF_SHORT_MOMENTUM_DAYS - 1] - 1
    )

    rankings = []
    asset_details = {}
    for code, candles in candles_by_code.items():
        completed = [
            candle for candle in candles
            if str(candle.get("date", "")).replace("-", "") != today
            and float(candle.get("close") or 0) > 0
        ]
        if len(completed) < needed:
            asset_details[code] = {
                "name": SECTOR_ETF_ASSETS.get(code, code),
                "eligible": False, "reason": f"확정 일봉 {needed}개 미만",
            }
            continue
        closes = [float(item["close"]) for item in completed]
        close = closes[-1]
        ma_now = sum(closes[-SECTOR_ETF_MA_PERIOD:]) / SECTOR_ETF_MA_PERIOD
        previous_end = -SECTOR_ETF_SLOPE_DAYS
        ma_previous = (
            sum(closes[previous_end-SECTOR_ETF_MA_PERIOD:previous_end])
            / SECTOR_ETF_MA_PERIOD
        )
        short_return = close / closes[-SECTOR_ETF_SHORT_MOMENTUM_DAYS - 1] - 1
        long_return = close / closes[-SECTOR_ETF_LONG_MOMENTUM_DAYS - 1] - 1
        relative_strength = short_return - benchmark_return
        score = short_return * 0.4 + long_return * 0.6
        eligible = (
            close > ma_now * (1 + SECTOR_ETF_BAND_PCT)
            and ma_now > ma_previous
            and short_return > 0 and long_return > 0
            and relative_strength > 0
        )
        detail = {
            "name": SECTOR_ETF_ASSETS.get(code, code),
            "previous_close": close,
            "ma": ma_now,
            "ma_5d_ago": ma_previous,
            "short_return_pct": round(short_return * 100, 2),
            "long_return_pct": round(long_return * 100, 2),
            "relative_strength_pct": round(relative_strength * 100, 2),
            "score": score,
            "score_pct": round(score * 100, 2),
            "eligible": eligible,
            "reason": "60일선 상승·1/3개월 양수·KODEX 200 대비 강세",
        }
        asset_details[code] = detail
        if eligible:
            rankings.append((score, code))

    rankings.sort(reverse=True)
    if not rankings:
        return "cash", None, {
            "reason": "시장보다 강한 상승 섹터 없음", "assets": asset_details,
        }
    best_score, target_code = rankings[0]
    if held_code and held_code != target_code:
        held_detail = asset_details.get(held_code, {})
        if (
            held_detail.get("eligible")
            and best_score < float(held_detail.get("score", 0))
                + SECTOR_ETF_SWITCH_MARGIN
        ):
            target_code = held_code
    target = asset_details[target_code]
    return "asset", target_code, {
        **target,
        "target_name": target["name"],
        "assets": asset_details,
        "reason": (
            "전일 확정 1/3개월 모멘텀·KODEX 200 상대강도·"
            f"교체 완충 {SECTOR_ETF_SWITCH_MARGIN*100:.0f}%p"
        ),
    }

def rotation_alloc_pct(code):
    """추세전환 신규 진입 비중: 인버스는 별도 상한을 적용한다."""
    if str(code) in ETF_INVERSE_CODES:
        return min(ETF_ROTATION_ALLOC_PCT, ETF_INVERSE_MAX_ALLOC_PCT)
    return ETF_ROTATION_ALLOC_PCT

def etf_rotation_traded_today(trades, today):
    return any(
        trade.get("strategy") == ETF_ROTATION_STRATEGY
        and str(trade.get("timestamp", "")).startswith(today)
        for trade in trades
    )

def sector_etf_traded_today(trades, today):
    return any(
        trade.get("strategy") == SECTOR_ETF_STRATEGY
        and str(trade.get("timestamp", "")).startswith(today)
        for trade in trades
    )

def market_session_progress(now=None):
    now = now or datetime.datetime.now()
    market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    total = (market_close - market_open).total_seconds()
    return max(0.0, min(1.0, (now - market_open).total_seconds() / total))

def run_trading_loop():
    global CURRENT_MARKET_OPEN
    if CORE_ALLOCATION_ENABLED:
        conflicts = core_code_conflicts()
        weight_sum = sum(weight for _, weight in CORE_TARGETS.values())
        if conflicts or abs(weight_sum - 1.0) > 1e-6:
            raise SystemExit(
                f"코어 자산배분 설정 오류: 다른 전략과 겹치는 종목 {conflicts}, "
                f"비중 합 {weight_sum:.3f} (1.0이어야 함)"
            )
    print("=" * 60)
    print("🎯 키움증권 퀀트 스나이퍼 봇 가동 시작 (동적 스크리닝 & ATR 손익비 엔진)")
    print(f"   [비용 설정] 거래세: {TRANSACTION_TAX_PCT*100:.2f}% | 수수료: {BROKER_COMMISSION_PCT*100:.3f}%×2 | 슬리피지: {SLIPPAGE_ESTIMATE_PCT*100:.2f}%×2")
    print(f"   [왕복 총비용] 약 {TOTAL_ROUNDTRIP_COST_PCT*100:.2f}% 실질 차감 적용")
    print("=" * 60)
    
    # 깃 푸시 권한 사전 점검
    git_ok = check_git_permission()
    
    # 가동 헬스체크 신호 기록. 원격 상태는 dashboard 브랜치로 통합한다.
    update_health_status(status="running", detail="봇 최초 가동 시작")
    
    api = KiwoomRestAPI()
    realtime = KiwoomRealtimeFeed(api.get_token)
    realtime.start()
    # 입출금 내역 API 응답 형식 점검(조회 전용, 금액·적요만 로컬 로그에 남김).
    try:
        check_end = datetime.date.today()
        check_flows = api.get_cash_flows(
            check_end.isoformat(), (check_end - datetime.timedelta(days=30)).isoformat()
        )
        api.get_cash_balance(force_refresh=True)
        print(f"[CASHFLOW] 출금가능 {api.withdrawable} / D+1 출금가능 {api.withdrawable_d1}", flush=True)
        if check_flows is None:
            print("[CASHFLOW] 입출금 내역 조회 실패: 잔고 추정 방식으로 동작", flush=True)
        else:
            print(f"[CASHFLOW] 최근 30일 입출금 {len(check_flows)}건 확인", flush=True)
            for flow in check_flows[-5:]:
                print(f"[CASHFLOW]   {flow['id'][:8]} {flow['amount']:+,.0f} {flow['memo']}", flush=True)
    except Exception as exc:
        print(f"[CASHFLOW] 입출금 내역 점검 오류: {exc}", flush=True)
    last_screened_date = None
    active_universe = DEFAULT_UNIVERSE if STOCK_STRATEGIES_ENABLED else {}
    universe_metadata = {}
    risk_date = None
    opening_assets = None
    trading_halted = False
    last_published_state = ("running", "봇 최초 가동 시작")
    signal_confirmations = {}
    last_signal_snapshot = None
    daily_candle_cache = {}
    peak_assets = None
    entry_size_multiplier = 1.0
    dashboard_dirty = True
    dashboard_critical = False
    last_dashboard_push_time = 0
    dashboard_started_at = time.time()
    last_intraday_scan_time = 0.0
    intraday_surge_universe = {}
    intraday_surge_metadata = {}
    intraday_surge_names = set()
    try:
        running_commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=BASE_DIR,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        running_commit = "unknown"
    dashboard_context = {
        **restore_dashboard_context(load_json(DASHBOARD_STATE_FILE, {})),
        "commit_hash": running_commit,
        "entry_size_multiplier": 1.0,
        "intraday_surge_count": 0,
    }
    mim_state = load_json(MIM_STATE_FILE, {})
    record_dashboard_event("SYSTEM", reason="봇 프로세스 시작")
    
    while True:
        try:
            now = datetime.datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            now_str = now.strftime("%Y-%m-%d %H:%M:%S")
            risk_reasons = []
            dashboard_context.update({
                "raw_signal_count": 0,
                "confirming_signal_count": 0,
                "confirmed_signal_count": 0,
                "signal_progress": [],
            })
            
            # 매일 아침 전일(D-1) 확정 거래대금 TOP 우량주 동적 스크리닝 갱신 (Lookahead 방지)
            universe_cache = load_json(UNIVERSE_FILE, {})
            cache_symbols = universe_cache.get("symbols", {})
            cache_metadata = universe_cache.get("metadata", {})
            cache_complete = bool(cache_symbols) and all(
                name in cache_metadata and cache_metadata[name].get("market")
                for name in cache_symbols
            )
            if (
                universe_cache.get("date") == today_str
                and universe_cache.get("strategy_version") == UNIVERSE_STRATEGY_VERSION
                and cache_symbols
            ):
                active_universe = {
                    **universe_cache["symbols"], **intraday_surge_universe
                }
                universe_metadata = {
                    **cache_metadata, **intraday_surge_metadata
                }
                if cache_complete:
                    last_screened_date = today_str
            dashboard_context.update({
                "universe_date": universe_cache.get("date", "-"),
                "universe_source": universe_cache.get(
                    "source", "kiwoom_previous_day" if universe_cache.get("symbols") else "-"
                ),
                "universe_count": len(active_universe),
                "universe_names": list(active_universe.keys()),
                "universe_symbols": [
                    {"name": name, "code": code,
                     "market": universe_metadata.get(name, {}).get("market", "미분류"),
                     "sector": universe_metadata.get(name, {}).get("sector", "미분류")}
                    for name, code in active_universe.items()
                ],
            })
            realtime.set_symbols(
                list(active_universe.values())
                + [MARKET_PROXY_CODE, KOSDAQ_MARKET_PROXY_CODE]
                + list(ETF_ROTATION_ASSETS)
                + list(SECTOR_ETF_ASSETS)
                + ([PARKING_ETF_CODE] if PARKING_ENABLED else [])
                + (list(CORE_TARGETS) if CORE_ALLOCATION_ENABLED else [])
                + list(core_holdings(load_json(CORE_STATE_FILE, {})))
                + [p.get("code") for p in load_json(POSITIONS_FILE, {}).values()]
            )
            proxy_quote = (
                realtime.quote(MARKET_PROXY_CODE)
                or realtime.quote(KOSDAQ_MARKET_PROXY_CODE)
            )
            if proxy_quote:
                dashboard_context["last_quote_at"] = datetime.datetime.fromtimestamp(
                    proxy_quote.get("received_at", time.time())
                ).strftime("%Y-%m-%d %H:%M:%S")
            CURRENT_MARKET_OPEN = is_market_open(
                now, bool(proxy_quote and proxy_quote.get("market_type") == "2")
            )

            # 장전에는 키움 전일 확정 순위를 미리 캐시한다.
            screening_window = now.weekday() < 5 and now.hour == 8 and now.minute >= 30
            if (DYNAMIC_SCREENING and STOCK_STRATEGIES_ENABLED
                    and last_screened_date != today_str and screening_window):
                print(f"[{now_str}] 🔍 전일(D-1) 확정 거래대금 TOP 우량주 동적 스크리닝 가동...")
                screening_account = api.get_account_snapshot()
                screening_parking = load_json(PARKING_STATE_FILE, {})
                screening_account = account_with_parking_cash(
                    screening_account, screening_parking,
                    parking_reference_price(
                        screening_parking, realtime.quote(PARKING_ETF_CODE)
                    ),
                )
                preferred_max_price = screening_preferred_max_price(screening_account)
                screened = api.get_previous_day_universe(
                    SCREENER_TOP_N, MIN_MARKET_CAP_KRW, MAX_MARKET_CAP_KRW,
                    preferred_max_price, SCREENER_RANK_POOL,
                )
                if screened:
                    active_universe = screened
                    universe_metadata = api.last_universe_metadata
                    last_screened_date = today_str
                    universe_source = "kiwoom_previous_day_rsi_vbo_mixed"
                    save_json(UNIVERSE_FILE, {
                        "date": today_str, "symbols": screened,
                        "metadata": universe_metadata,
                        "source": universe_source,
                        "strategy_version": UNIVERSE_STRATEGY_VERSION,
                    })
                    dashboard_context.update({
                        "universe_date": today_str,
                        "universe_source": universe_source,
                        "universe_count": len(screened),
                        "universe_symbols": [
                            {"name": name, "code": code,
                             "market": universe_metadata.get(name, {}).get(
                                 "market", "미분류"
                             ),
                             "sector": universe_metadata.get(name, {}).get(
                                 "sector", "미분류"
                             )}
                            for name, code in screened.items()
                        ],
                    })
                    print(f"[{now_str}] ✅ 오늘자 감시 유니버스 {len(active_universe)}종목 갱신 완료:")
                    print("   ", ", ".join(list(active_universe.keys())[:10]), "...")
                elif not STOCK_STRATEGIES_ENABLED:
                    # 개별주를 쓰지 않으면 유니버스 실패가 ETF 주문을 막지 않는다.
                    active_universe = {}
                    print(f"[{now_str}] [INFO] D-1 스크리닝 실패(개별주 비활성화라 주문 영향 없음)")
                else:
                    active_universe = {}
                    risk_reasons.append("D-1 스크리닝 실패")
                    trading_halted = True
                    alert_detail = "D-1 스크리닝 실패: 오늘 신규 주문 중단"
                    update_health_status("halted", alert_detail)
                    record_dashboard_event("RISK", reason=alert_detail)
                    dashboard_dirty = True
                    dashboard_critical = True
                    print(f"[{now_str}] [WARN] D-1 스크리닝 실패: 오늘 신규매수를 차단합니다.")

            # 장중 최초 기동 시에도 키움의 '전일' 확정 순위로만 오늘 유니버스를 복구한다.
            intraday_bootstrap = (
                DYNAMIC_SCREENING
                and STOCK_STRATEGIES_ENABLED
                and last_screened_date != today_str
                and CURRENT_MARKET_OPEN
                and is_entry_window(now)
            )
            if intraday_bootstrap:
                print(f"[{now_str}] 🔄 키움 전일 거래대금 기준 장중 유니버스 복구...")
                screening_account = api.get_account_snapshot()
                screening_parking = load_json(PARKING_STATE_FILE, {})
                screening_account = account_with_parking_cash(
                    screening_account, screening_parking,
                    parking_reference_price(
                        screening_parking, realtime.quote(PARKING_ETF_CODE)
                    ),
                )
                preferred_max_price = screening_preferred_max_price(screening_account)
                screened = api.get_previous_day_universe(
                    SCREENER_TOP_N, MIN_MARKET_CAP_KRW, MAX_MARKET_CAP_KRW,
                    preferred_max_price, SCREENER_RANK_POOL,
                )
                if screened:
                    active_universe = screened
                    universe_metadata = api.last_universe_metadata
                    last_screened_date = today_str
                    universe_source = "kiwoom_previous_day_rsi_vbo_mixed"
                    save_json(UNIVERSE_FILE, {
                        "date": today_str, "symbols": screened,
                        "metadata": universe_metadata,
                        "source": universe_source,
                        "strategy_version": UNIVERSE_STRATEGY_VERSION,
                    })
                    dashboard_context.update({
                        "universe_date": today_str,
                        "universe_source": universe_source,
                        "universe_count": len(screened),
                        "universe_symbols": [
                            {"name": name, "code": code,
                             "market": universe_metadata.get(name, {}).get(
                                 "market", "미분류"
                             ),
                             "sector": universe_metadata.get(name, {}).get(
                                 "sector", "미분류"
                             )}
                            for name, code in screened.items()
                        ],
                    })
                    print(f"[{now_str}] ✅ 장중 유니버스 {len(screened)}종목 복구 완료")
                elif not STOCK_STRATEGIES_ENABLED:
                    print(f"[{now_str}] [INFO] 장중 유니버스 복구 실패(개별주 비활성화라 주문 영향 없음)")
                else:
                    trading_halted = True
                    risk_reasons.append("키움 전일 유니버스 복구 실패")
                    print(f"[{now_str}] [RISK] 장중 유니버스 복구 실패: 신규 주문 중단")
                    
            # 장 운영 시간 확인
            if not CURRENT_MARKET_OPEN:
                # 장중 재시작 직후에는 실시간 정규장 체결이 아직 안 들어와 장외로 보일 수 있다.
                # 이때 CLOSED 대시보드를 게시하지 않고 실시간 확인을 잠시 기다린다.
                if (
                    is_market_open(now, live_status=True)
                    and time.time() - dashboard_started_at < STARTUP_LIVE_WAIT_SECONDS
                ):
                    update_health_status(status="running", detail="재시작 후 실시간 시세 확인 중")
                    time.sleep(5)
                    continue
                print(f"[{now_str}] 정규장 운영 시간 외입니다. 대기 중...")
                # 휴장일에도 루프 생존 신호를 남겨 watchdog이 재시작을 반복하지 않게 한다.
                update_health_status(status="running", detail="정규장 운영 시간 외")
                closed_positions = load_json(POSITIONS_FILE, {})
                if restore_position_last_prices(api, closed_positions, today_str):
                    save_json(POSITIONS_FILE, closed_positions)
                    dashboard_dirty = True
                closed_risk_state = load_json(RISK_FILE, {})
                restored_metrics, risk_changed = restore_daily_account_metrics(
                    api, closed_risk_state, today_str
                )
                if restored_metrics:
                    dashboard_context.update(restored_metrics)
                if risk_changed:
                    save_json(RISK_FILE, closed_risk_state)
                    dashboard_context["last_account_sync"] = now_str
                    dashboard_dirty = True
                # 장외에도 코어·비중을 마지막 확인가 기준으로 갱신한다.
                closed_baseline = (load_json(RISK_FILE, {}) or {}).get("account_baseline") or {}
                closed_total_assets = float(closed_baseline.get("total_assets") or 0)
                if closed_total_assets > 0:
                    closed_core_state = load_json(CORE_STATE_FILE, {})
                    closed_prices = {
                        code: (realtime.quote(code, DISPLAY_QUOTE_MAX_AGE_SECONDS) or {}).get("price")
                        or (closed_baseline.get("positions", {}).get(code) or {}).get("current_price")
                        for code in CORE_TARGETS
                    }
                    dashboard_context["core"] = core_dashboard_info(
                        closed_core_state, closed_prices, closed_total_assets,
                    )
                    dashboard_context["position_weights"] = position_weight_pcts(
                        closed_positions, realtime, closed_total_assets,
                    )
                    realtime_connected, _ = realtime.is_healthy()
                startup_wait_done = time.time() - dashboard_started_at >= 60
                should_publish = (
                    (dashboard_dirty and (realtime_connected or startup_wait_done))
                    or is_dashboard_heartbeat_due(last_dashboard_push_time)
                )
                if should_publish:
                    if sync_dashboard_to_branch(
                        "running", "정규장 운영 시간 외",
                        closed_positions, realtime, dashboard_context,
                    ):
                        dashboard_dirty = False
                        last_dashboard_push_time = time.time()
                time.sleep(POLL_INTERVAL_SECONDS)
                continue
                
            positions = load_json(POSITIONS_FILE, {})
            trades = load_json(TRADES_FILE, [])
            parking = load_json(PARKING_STATE_FILE, {})
            core_state = load_json(CORE_STATE_FILE, {})
            # 코어 보유일도 매매 포지션처럼 장이 열린 거래일 단위로 센다.
            core_days_changed = False
            for core_item in core_holdings(core_state).values():
                if core_item.get("last_held_date") != today_str:
                    trading_days_held(core_item, today_str)
                    core_days_changed = True
            if core_days_changed:
                save_json(CORE_STATE_FILE, core_state)

            # 실계좌가 상태의 원본이다. 조회 실패는 해당 루프의 신규 주문만 차단한다.
            # 다음 조회가 정상이면 영구 안전잠금과 구분해 즉시 거래를 재개한다.
            account = api.get_account_snapshot()
            account_lookup_failed = not account.get("ok")
            risk_state = load_json(RISK_FILE, {})
            if peak_assets is None:
                peak_assets = risk_state.get("peak_assets") or None
            if account_lookup_failed:
                risk_reasons.append(f"계좌 조회 실패: {account.get('error', 'unknown')}")
                print(f"[{now_str}] [RISK] 계좌 조회 실패로 이번 루프의 신규 주문을 중단합니다.")
            elif risk_date != today_str:
                if risk_date is not None:
                    intraday_surge_universe = {}
                    intraday_surge_metadata = {}
                    intraday_surge_names = set()
                    last_intraday_scan_time = 0.0
                risk_date = today_str
                if risk_state.get("date") == today_str:
                    opening_assets = risk_state.get("opening_assets") or None
                    peak_assets = risk_state.get("peak_assets") or peak_assets
                    trading_halted = bool(risk_state.get("halted", False))
                    if trading_halted:
                        risk_reasons.append(f"지속 중인 거래 중단: {risk_state.get('reason', 'unknown')}")
                else:
                    # 전일 마지막 계좌 스냅샷을 기준으로 시작해야 장외(밤사이·장전) 입출금을
                    # 아래 입출금 보정 단계에서 잡을 수 있다. 오늘 첫 스냅샷으로 기준을 덮으면
                    # 장전 입금이 수익으로 잡히고 코어 재조정도 예약되지 않는다.
                    prev_baseline = risk_state.get("account_baseline") or {}
                    opening_assets = (
                        float(prev_baseline.get("total_assets") or 0)
                        or account.get("total_assets") or None
                    )
                    trading_halted = False
                    # 누적 원금·TWR 기준값은 자정 초기화 뒤에도 이어진다.
                    risk_state = {
                        **preserve_cumulative_risk_fields(risk_state),
                        "date": today_str, "opening_assets": opening_assets,
                        "peak_assets": peak_assets,
                        "halted": False,
                        "account_baseline": prev_baseline or account_snapshot_baseline(account),
                    }
                    save_json(RISK_FILE, risk_state)

            if account.get("ok"):
                dashboard_context["last_account_sync"] = now_str

            if account.get("ok") and opening_assets:
                current_assets = account.get("total_assets", opening_assets)
                external_flow = resolve_external_flow(
                    api, risk_state, today_str,
                    estimate_external_cash_flow(risk_state.get("account_baseline"), account),
                    current_assets,
                    traded=holdings_changed(risk_state.get("account_baseline"), account),
                )
                if external_flow:
                    if mark_flow_rebalance(core_state, external_flow, current_assets):
                        save_json(CORE_STATE_FILE, core_state)
                        record_dashboard_event(
                            "CORE", reason="큰 입출금 감지: 코어 리밸런싱 예약",
                        )
                    opening_assets = max(0.0, float(opening_assets) + external_flow)
                    peak_assets = max(
                        float(current_assets), float(peak_assets or 0) + external_flow
                    )
                    dashboard_context["last_external_flow"] = {
                        "timestamp": now_str,
                        "amount": round(external_flow, 0),
                    }
                    record_dashboard_event(
                        "SYSTEM", reason="외부 입출금 추정치로 손익 기준 보정",
                        amount=round(external_flow, 0),
                    )
                    print(
                        f"[{now_str}] [ACCOUNT] 외부 입출금 {external_flow:+,.0f}원 추정; "
                        "시작자산·계좌고점 보정"
                    )
                peak_assets, account_drawdown, entry_size_multiplier, drawdown_halted = (
                    account_drawdown_controls(current_assets, peak_assets)
                )
                risk_state.update({
                    "date": today_str, "opening_assets": opening_assets,
                    "peak_assets": peak_assets,
                    "account_baseline": account_snapshot_baseline(account),
                })
                if drawdown_halted:
                    trading_halted = True
                    risk_state.update({"halted": True, "reason": "equity_drawdown_limit"})
                    risk_reasons.append(
                        f"계좌 고점 대비 낙폭 한도 도달: -{account_drawdown*100:.2f}%"
                    )
                elif account_drawdown >= EQUITY_SLOWDOWN_DRAWDOWN_PCT:
                    risk_reasons.append(
                        f"계좌 낙폭 감속 적용: -{account_drawdown*100:.2f}% (주문 50%)"
                    )
                daily_return = (
                    (current_assets - opening_assets) / opening_assets
                    if opening_assets > 0 else 0.0
                )
                cumulative_metrics = update_cumulative_account_metrics(
                    risk_state, current_assets,
                    realized_net_profit_total(trades),
                    external_flow=external_flow,
                )
                risk_state.update({
                    "daily_return_pct": daily_return * 100,
                    "account_drawdown_pct": account_drawdown * 100,
                    "entry_size_multiplier": entry_size_multiplier,
                })
                save_json(RISK_FILE, risk_state)
                dashboard_context.update({
                    "account_drawdown_pct": account_drawdown * 100,
                    "daily_return_pct": daily_return * 100,
                    "entry_size_multiplier": entry_size_multiplier,
                    **cumulative_metrics,
                })
                if daily_return <= -DAILY_KILL_SWITCH_PCT:
                    trading_halted = True
                    risk_state.update({
                        "halted": True, "reason": "daily_loss_limit",
                    })
                    save_json(RISK_FILE, risk_state)
                    risk_reasons.append(f"일일 손실 한도 도달: {daily_return*100:.2f}%")
                    print(f"[{now_str}] [KILL SWITCH] 일일 손실 {daily_return*100:.2f}%로 신규 주문 중단")

            # 브로커에만 있거나 로컬에만 있는 포지션은 자동 추정하지 않고 신규매수를 차단한다.
            if account.get("ok"):
                broker_codes = set(account["positions"])
                local_codes = {p.get("code") for p in positions.values()}
                if parking_shares(parking) > 0:
                    local_codes.add(PARKING_ETF_CODE)
                broker_parking_qty = int(
                    account["positions"].get(PARKING_ETF_CODE, {}).get("shares", 0)
                )
                for core_code in set(CORE_TARGETS) | set(core_holdings(core_state)):
                    local_core_qty = core_shares(core_state, core_code)
                    if local_core_qty > 0:
                        local_codes.add(core_code)
                    broker_core_qty = int(
                        account["positions"].get(core_code, {}).get("shares", 0)
                    )
                    if broker_core_qty != local_core_qty:
                        trading_halted = True
                        risk_reasons.append(f"코어 자산배분 수량 불일치({core_code})")
                        print(f"[{now_str}] [RISK] 코어 자산배분 {core_code} 수량 불일치로 신규 주문 중단 "
                              f"(broker={broker_core_qty}, local={local_core_qty})")
                if broker_parking_qty != parking_shares(parking):
                    trading_halted = True
                    risk_reasons.append("대기자금 ETF 수량 불일치")
                    print(f"[{now_str}] [RISK] 대기자금 ETF 수량 불일치로 신규 주문 중단 "
                          f"(broker={broker_parking_qty}, local={parking_shares(parking)})")
                if broker_codes != local_codes:
                    trading_halted = True
                    risk_reasons.append("계좌/로컬 포지션 불일치")
                    print(f"[{now_str}] [RISK] 계좌/로컬 포지션 불일치로 신규 주문 중단 "
                          f"(broker={sorted(broker_codes)}, local={sorted(local_codes)})")

            # 전일 유니버스 밖에서 당일 새로 거래가 몰린 종목을 5분마다 소수 보강한다.
            intraday_scan_due = (
                INTRADAY_SURGE_ENABLED
                and account.get("ok")
                and is_entry_window(now)
                and time.time() - last_intraday_scan_time
                    >= INTRADAY_SURGE_SCAN_INTERVAL_SECONDS
            )
            if intraday_scan_due:
                last_intraday_scan_time = time.time()
                surge_universe = api.get_intraday_surge_universe(
                    INTRADAY_SURGE_MAX_CANDIDATES,
                    MIN_MARKET_CAP_KRW, MAX_MARKET_CAP_KRW,
                    intraday_surge_max_price(account_with_parking_cash(
                        account, parking,
                        parking_reference_price(
                            parking, realtime.quote(PARKING_ETF_CODE)
                        ),
                    )),
                    INTRADAY_SURGE_MIN_DAY_RETURN_PCT,
                    INTRADAY_SURGE_MAX_DAY_RETURN_PCT,
                    INTRADAY_SURGE_MIN_VOLUME_RATE_PCT,
                )
                intraday_surge_universe = surge_universe
                intraday_surge_metadata = api.last_intraday_metadata
                intraday_surge_names = set(surge_universe)
                base_symbols = universe_cache.get("symbols", {})
                base_metadata = universe_cache.get("metadata", {})
                active_universe = {**base_symbols, **intraday_surge_universe}
                universe_metadata = {**base_metadata, **intraday_surge_metadata}
                realtime.set_symbols(
                    list(active_universe.values())
                    + [MARKET_PROXY_CODE, KOSDAQ_MARKET_PROXY_CODE]
                    + list(ETF_ROTATION_ASSETS)
                    + list(SECTOR_ETF_ASSETS)
                    + ([PARKING_ETF_CODE] if PARKING_ENABLED else [])
                    + (list(CORE_TARGETS) if CORE_ALLOCATION_ENABLED else [])
                    + list(core_holdings(core_state))
                    + [p.get("code") for p in positions.values()]
                )
                dashboard_context.update({
                    "intraday_surge_count": len(intraday_surge_names),
                    "intraday_surge_names": sorted(intraday_surge_names),
                    "universe_count": len(active_universe),
                    "universe_names": list(active_universe),
                    "universe_symbols": [
                        {"name": name, "code": code,
                         "market": universe_metadata.get(name, {}).get(
                             "market", "미분류"
                         ),
                         "sector": universe_metadata.get(name, {}).get(
                             "sector", "미분류"
                         )}
                        for name, code in active_universe.items()
                    ],
                })
                dashboard_dirty = True
                print(
                    f"[{now_str}] [INTRADAY] 거래량 급증 후보 "
                    f"{len(intraday_surge_names)}종목: "
                    f"{', '.join(sorted(intraday_surge_names)) or '-'}"
                )
            
            # 1. 캔들 데이터 조회
            candles_dict = {}
            for name, code in active_universe.items():
                cache_miss = code not in daily_candle_cache
                c = get_kiwoom_candles(api, realtime, daily_candle_cache, code, now)
                if c:
                    candles_dict[name] = c
                if cache_miss:
                    time.sleep(API_REQUEST_DELAY)

            # 신규 후보와 무관하게 모든 보유 종목을 반드시 감시한다.
            for name, pos in positions.items():
                if name in candles_dict:
                    continue
                cache_miss = pos["code"] not in daily_candle_cache
                c = get_kiwoom_candles(
                    api, realtime, daily_candle_cache, pos["code"], now
                )
                if c:
                    candles_dict[name] = c
                if cache_miss:
                    time.sleep(API_REQUEST_DELAY)

            kospi_market_candles = get_kiwoom_candles(
                api, realtime, daily_candle_cache, MARKET_PROXY_CODE, now
            )
            kosdaq_market_candles = get_kiwoom_candles(
                api, realtime, daily_candle_cache, KOSDAQ_MARKET_PROXY_CODE, now
            )
            rotation_candles = {}
            for rotation_code in ETF_ROTATION_ASSETS:
                cache_miss = rotation_code not in daily_candle_cache
                rotation_candles[rotation_code] = get_kiwoom_candles(
                    api, realtime, daily_candle_cache, rotation_code, now, count=130
                )
                if cache_miss:
                    time.sleep(API_REQUEST_DELAY)
            sector_candles = {}
            for sector_code in SECTOR_ETF_ASSETS:
                cache_miss = sector_code not in daily_candle_cache
                sector_candles[sector_code] = get_kiwoom_candles(
                    api, realtime, daily_candle_cache, sector_code, now, count=70
                )
                if cache_miss:
                    time.sleep(API_REQUEST_DELAY)
            market_regimes = {
                "KOSPI": is_favorable_market(kospi_market_candles),
                "KOSDAQ": is_favorable_market(kosdaq_market_candles),
            }
            proxy_quote = realtime.quote(MARKET_PROXY_CODE)
            previous_proxy_close = 0
            if kospi_market_candles and len(kospi_market_candles) >= 2:
                previous_proxy_close = kospi_market_candles[-2]["close"]
            if MIM_ENABLED:
                mim_state = update_mim_state(
                    mim_state, now, proxy_quote, previous_proxy_close
                )
                save_json(MIM_STATE_FILE, mim_state)
                dashboard_context["mim_state"] = {
                    "ready": all(
                        float(mim_state.get("snapshots", {}).get(key) or 0) > 0
                        for key in (
                            "previous_close", "open", "0930", "1000", "1430", "1500"
                        )
                    ),
                    "long_signal": mim_long_signal(mim_state),
                    "entry_attempted": bool(mim_state.get("entry_attempted")),
                }
            existing_rotation_position = next(
                (
                    pos for pos in positions.values()
                    if pos.get("strategy") == ETF_ROTATION_STRATEGY
                ),
                None,
            )
            rotation_regime, rotation_target_code, rotation_detail = (
                multi_asset_trend_selection(
                    rotation_candles,
                    existing_rotation_position.get("code")
                    if existing_rotation_position else None,
                    now,
                )
            )
            dashboard_context["etf_rotation"] = {
                "enabled": ETF_ROTATION_ENABLED,
                "regime": rotation_regime,
                "target_code": rotation_target_code,
                "held": bool(existing_rotation_position),
                **rotation_detail,
            }
            existing_sector_position = next(
                (
                    pos for pos in positions.values()
                    if pos.get("strategy") == SECTOR_ETF_STRATEGY
                ),
                None,
            )
            sector_regime, sector_target_code, sector_detail = (
                sector_etf_trend_selection(
                    sector_candles, kospi_market_candles,
                    existing_sector_position.get("code")
                    if existing_sector_position else None,
                    now,
                )
            )
            dashboard_context["sector_etf"] = {
                "enabled": SECTOR_ETF_ENABLED,
                "regime": sector_regime,
                "target_code": sector_target_code,
                "held": bool(existing_sector_position),
                **sector_detail,
            }
            # 대기자금 ETF는 전략 계산에서 현금으로 본다.
            parking_price = parking_reference_price(
                parking, realtime.quote(PARKING_ETF_CODE)
            )
            strategy_account = account_with_parking_cash(
                account, parking, parking_price
            )
            dashboard_context["parking"] = (
                parking_dashboard_info(
                    parking, parking_price,
                    float(account.get("total_assets") or 0),
                )
                if account.get("ok") else dashboard_context.get("parking")
            )
            if account.get("ok"):
                dashboard_context["core"] = core_dashboard_info(
                    core_state,
                    {
                        code: (realtime.quote(code, DISPLAY_QUOTE_MAX_AGE_SECONDS) or {}).get("price")
                        for code in CORE_TARGETS
                    },
                    float(account.get("total_assets") or 0),
                )
            etf_tradeability = []
            if account.get("ok"):
                etf_codes = list(dict.fromkeys(
                    list(ETF_ROTATION_ASSETS) + list(SECTOR_ETF_ASSETS)
                ))
                etf_quotes = {
                    code: realtime.quote(code) for code in etf_codes
                }
                etf_tradeability = build_etf_tradeability(
                    ETF_ROTATION_ASSETS, "다중자산 ETF", ETF_ROTATION_ALLOC_PCT,
                    ETF_ROTATION_STOP_LOSS_PCT,
                    float(account.get("total_assets") or 0),
                    float(strategy_account.get("cash") or 0), etf_quotes,
                    entry_size_multiplier,
                    risk_pct=ETF_MAX_RISK_PER_TRADE_PCT,
                ) + build_etf_tradeability(
                    SECTOR_ETF_ASSETS, "섹터 ETF", SECTOR_ETF_ALLOC_PCT,
                    SECTOR_ETF_STOP_LOSS_PCT,
                    float(account.get("total_assets") or 0),
                    float(strategy_account.get("cash") or 0), etf_quotes,
                    entry_size_multiplier,
                    allow_one_share_over_allocation=True,
                )
            dashboard_context["etf_tradeability"] = etf_tradeability
            etf_tradeability_by_code = {
                item["code"]: item for item in etf_tradeability
            }
            kosdaq_names = {
                name for name, meta in universe_metadata.items()
                if "KOSDAQ" in str(meta.get("market", "")).upper()
            }
            kospi_candles = {
                name: candles for name, candles in candles_dict.items()
                if name not in kosdaq_names
            }
            kosdaq_candles = {
                name: candles for name, candles in candles_dict.items()
                if name in kosdaq_names
            }
            progress = market_session_progress(now)
            signal_diagnostics = {}
            raw_signals = evaluate_signals(
                kospi_candles, kospi_market_candles, progress,
                signal_diagnostics,
            ) + evaluate_signals(
                kosdaq_candles, kosdaq_market_candles, progress,
                signal_diagnostics,
            )
            raw_signals = apply_intraday_surge_limits(
                raw_signals, intraday_surge_names
            )
            rotation_position = existing_rotation_position
            rotation_blocked_today = etf_rotation_traded_today(trades, today_str)
            rotation_quote = (
                realtime.quote(rotation_target_code) if rotation_target_code else None
            )
            dashboard_context["etf_rotation"]["traded_today"] = rotation_blocked_today
            rotation_gap_allowed, rotation_gap_pct = etf_entry_gap_status(
                rotation_detail.get("previous_close"),
                rotation_quote.get("price") if rotation_quote else None,
            )
            dashboard_context["etf_rotation"].update({
                "entry_gap_pct": (
                    round(rotation_gap_pct * 100, 2)
                    if rotation_gap_pct is not None else None
                ),
                "entry_gap_blocked": bool(
                    rotation_target_code and not rotation_gap_allowed
                ),
            })
            if (
                ETF_ROTATION_ENABLED and rotation_target_code
                and not rotation_position and not rotation_blocked_today
                and rotation_gap_allowed
                and etf_tradeability_by_code.get(
                    rotation_target_code, {}
                ).get("eligible", False)
            ):
                if rotation_quote:
                    raw_signals.append({
                        "name": ETF_ROTATION_NAMES[rotation_target_code],
                        "code": rotation_target_code,
                        "strategy": ETF_ROTATION_STRATEGY,
                        "score": -2.0,
                        "entry_price": float(rotation_quote["price"]),
                        "size_multiplier": 1.0,
                        "max_investment_pct": rotation_alloc_pct(rotation_target_code),
                        "stop_loss_pct": ETF_ROTATION_STOP_LOSS_PCT,
                        "risk_pct": ETF_MAX_RISK_PER_TRADE_PCT,
                        "take_profit_pct": 1.0,
                        "atr": float(rotation_quote["price"]) * ETF_ROTATION_STOP_LOSS_PCT,
                        "roundtrip_cost_pct": ETF_ROUNDTRIP_COST_PCT,
                        "order_exchange": "KRX",
                    })
            sector_position = existing_sector_position
            sector_blocked_today = sector_etf_traded_today(trades, today_str)
            dashboard_context["sector_etf"]["traded_today"] = sector_blocked_today
            sector_quote = (
                realtime.quote(sector_target_code) if sector_target_code else None
            )
            sector_gap_allowed, sector_gap_pct = etf_entry_gap_status(
                sector_detail.get("previous_close"),
                sector_quote.get("price") if sector_quote else None,
            )
            dashboard_context["sector_etf"].update({
                "entry_gap_pct": (
                    round(sector_gap_pct * 100, 2)
                    if sector_gap_pct is not None else None
                ),
                "entry_gap_blocked": bool(
                    sector_target_code and not sector_gap_allowed
                ),
            })
            if (
                SECTOR_ETF_ENABLED and sector_target_code
                and not sector_position and not sector_blocked_today
                and sector_gap_allowed
                and etf_tradeability_by_code.get(
                    sector_target_code, {}
                ).get("eligible", False)
            ):
                if sector_quote:
                    raw_signals.append({
                        "name": SECTOR_ETF_NAMES[sector_target_code],
                        "code": sector_target_code,
                        "strategy": SECTOR_ETF_STRATEGY,
                        "score": -1.5,
                        "entry_price": float(sector_quote["price"]),
                        "size_multiplier": 1.0,
                        "max_investment_pct": SECTOR_ETF_ALLOC_PCT,
                        "allow_one_share_over_allocation": True,
                        "stop_loss_pct": SECTOR_ETF_STOP_LOSS_PCT,
                        "take_profit_pct": 1.0,
                        "atr": float(sector_quote["price"]) * SECTOR_ETF_STOP_LOSS_PCT,
                        "roundtrip_cost_pct": ETF_ROUNDTRIP_COST_PCT,
                        "order_exchange": "KRX",
                    })
            if (
                MIM_ENABLED and is_mim_entry_window(now)
                and mim_long_signal(mim_state)
                and not mim_state.get("entry_attempted")
                and MIM_POSITION_NAME not in positions
                and proxy_quote
            ):
                raw_signals.append({
                    "name": MIM_POSITION_NAME,
                    "code": MARKET_PROXY_CODE,
                    "strategy": MIM_STRATEGY,
                    "score": -1.0,
                    "entry_price": float(proxy_quote["price"]),
                    "size_multiplier": 1.0,
                    "stop_loss_pct": MIM_STOP_LOSS_PCT,
                    "take_profit_pct": 1.0,
                    "atr": float(proxy_quote["price"]) * MIM_STOP_LOSS_PCT,
                    "max_shares": MIM_MAX_SHARES,
                })
            raw_signals.sort(key=lambda item: item["score"])
            market_favorable = any(market_regimes.values())
            monitored_names = list(active_universe)

            if account.get("ok"):
                tradeability = build_tradeability(
                    active_universe, universe_metadata, candles_dict,
                    float(account.get("total_assets") or 0),
                    float(strategy_account.get("cash") or 0), entry_size_multiplier,
                    market_regimes,
                )
                # 전일 기준 스크리닝을 통과했더라도 장중 급등으로 현재 계좌에서
                # 1주를 살 수 없으면 신호 평가·대시보드 감시에서 제외한다.
                monitored_names = [
                    item["name"] for item in tradeability if item.get("eligible")
                ]
                monitored_set = set(monitored_names)
                raw_signals = [
                    sig for sig in raw_signals
                    if sig["name"] in monitored_set
                    or sig.get("strategy") in {
                        MIM_STRATEGY, ETF_ROTATION_STRATEGY, SECTOR_ETF_STRATEGY
                    }
                ]
                dashboard_context["tradeability"] = [
                    item for item in tradeability if item.get("eligible")
                ]
                dashboard_context["universe_count"] = len(monitored_names)

            raw_signal_by_name = {sig["name"]: sig for sig in raw_signals}
            stock_block_reason = stock_entry_block_reason(
                now, rotation_target_code, positions
            )
            dashboard_context.update({
                "stock_entry_block_reason": stock_block_reason,
                "market_favorable": market_favorable,
                "market_regimes": market_regimes,
                "raw_signal_count": len(raw_signals),
                "signal_diagnostics": [
                    {"name": name, **signal_diagnostics.get(name, {})}
                    for name in monitored_names
                ],
                "signal_statuses": [
                    {
                        "name": name,
                        "active": name in raw_signal_by_name,
                        "strategy": raw_signal_by_name.get(name, {}).get("strategy"),
                        "size_multiplier": raw_signal_by_name.get(name, {}).get(
                            "size_multiplier", 1.0
                        ),
                        "count": signal_confirmations.get(
                            (name, raw_signal_by_name.get(name, {}).get("strategy")), 0
                        ) if name in raw_signal_by_name else 0,
                    }
                    for name in monitored_names
                ],
            })
                
            # 2. 보유 포지션 감시 및 청산 체크 (동적 손절, 트레일링 스탑, 익절)
            closed_positions = []
            for name, pos in list(positions.items()):
                is_mim_position = pos.get("strategy") == MIM_STRATEGY
                is_rotation_position = pos.get("strategy") == ETF_ROTATION_STRATEGY
                is_sector_position = pos.get("strategy") == SECTOR_ETF_STRATEGY
                if name not in candles_dict and not (
                    is_mim_position or is_rotation_position or is_sector_position
                ):
                    continue
                # 체결이 뜸한 종목도 청산 판단을 건너뛰지 않도록 최근 60초 체결가까지 인정한다.
                quote = realtime.quote(pos["code"], EXIT_QUOTE_MAX_AGE_SECONDS)
                if not quote:
                    risk_reasons.append(f"{name} 실시간 시세 없음")
                    print(f"[{now_str}] [RISK] {name} 실시간 시세가 없어 청산 판단을 보류합니다.")
                    continue
                trading_days_held(pos, today_str)
                buy_p = float(pos["buy_price"])
                curr_p = quote["price"]
                pos["last_price"] = curr_p
                pos["last_price_at"] = now_str
                exit_price = None
                exit_reason = None
                mim_exit_hour, mim_exit_minute = map(int, MIM_EXIT_TIME.split(":"))
                mim_exit_at = now.replace(
                    hour=mim_exit_hour, minute=mim_exit_minute,
                    second=0, microsecond=0,
                )
                if is_rotation_position and (
                    not ETF_ROTATION_ENABLED
                    or pos.get("code") != rotation_target_code
                ):
                    exit_price = curr_p
                    exit_reason = (
                        "ETF 추세전환 전략 비활성화 청산"
                        if not ETF_ROTATION_ENABLED
                        else f"ETF 국면 전환: {rotation_regime}"
                    )
                elif is_sector_position and (
                    not SECTOR_ETF_ENABLED
                    or pos.get("code") != sector_target_code
                ):
                    exit_price = curr_p
                    exit_reason = (
                        "섹터 ETF 전략 비활성화 청산"
                        if not SECTOR_ETF_ENABLED
                        else f"섹터 추세 전환: {sector_detail.get('target_name', '현금')}"
                    )
                elif is_mim_position and (
                    pos.get("entry_date") != today_str or now >= mim_exit_at
                ):
                    exit_price = curr_p
                    exit_reason = (
                        "장마감 모멘텀 이월 포지션 복구 청산"
                        if pos.get("entry_date") != today_str
                        else "장마감 모멘텀 당일 청산"
                    )
                else:
                    stop_price, stop_reason = update_position_stop(
                        pos, quote.get("high_water", curr_p)
                    )
                    if curr_p <= stop_price:
                        exit_price = stop_price
                        exit_reason = stop_reason
                    elif not (is_rotation_position or is_sector_position):
                        time_exit_reason = time_stop_reason(pos, curr_p)
                        if time_exit_reason:
                            exit_price = curr_p
                            exit_reason = time_exit_reason
                    
                if exit_price:
                    if account.get("ok"):
                        broker_qty = int(account["positions"].get(pos["code"], {}).get("shares", 0))
                        if broker_qty < pos["shares"]:
                            trading_halted = True
                            risk_reasons.append(f"{name} 실보유수량 불일치")
                            print(f"[{now_str}] [CRITICAL] {name} 실보유수량 불일치로 매도 주문 차단 "
                                  f"(broker={broker_qty}, local={pos['shares']})")
                            continue
                    before_qty = pos["shares"]
                    order = api.send_sell_order(
                        pos["code"], name, before_qty, exit_price, exit_reason,
                        exchange=pos.get("order_exchange", "SOR"),
                    )
                    actual_exit_price = exit_price
                    if order.get("status") == "filled":
                        confirmed = True
                    elif order.get("status") == "accepted":
                        fill = confirm_order_fill(
                            api, order.get("order_id"), pos["code"], before_qty
                        )
                        confirmed = fill.get("filled_qty", 0) >= before_qty
                        if confirmed:
                            actual_exit_price = fill["fill_price"]
                    else:
                        confirmed = False
                    if not confirmed:
                        trading_halted = True
                        risk_reasons.append(f"{name} 매도 체결 미확인")
                        print(f"[{now_str}] [CRITICAL] 매도 체결 미확인: {name}; 포지션 유지 및 신규주문 중단")
                        continue

                    gross_ret_pct = ((actual_exit_price - buy_p) / buy_p) * 100
                    roundtrip_cost_pct = float(
                        pos.get("roundtrip_cost_pct", TOTAL_ROUNDTRIP_COST_PCT)
                    )
                    cost_krw = pos["shares"] * actual_exit_price * roundtrip_cost_pct
                    gross_profit_krw = pos["shares"] * (actual_exit_price - buy_p)
                    net_profit_krw = gross_profit_krw - cost_krw
                    net_ret_pct = (net_profit_krw / (pos["shares"] * buy_p)) * 100
                    
                    trade_record = {
                        "timestamp": now_str,
                        "type": "SELL",
                        "name": name,
                        "code": pos["code"],
                        "shares": pos["shares"],
                        "entry_price": buy_p,
                        "exit_price": actual_exit_price,
                        "signal_price": float(curr_p),  # 주문 직전 실시간가(슬리피지 측정용)
                        "price_source": "broker_execution",
                        "gross_return_pct": round(gross_ret_pct, 2),
                        "net_return_pct": round(net_ret_pct, 2),
                        "cost_fee_krw": round(cost_krw, 0),
                        "net_profit_krw": round(net_profit_krw, 0),
                        "reason": exit_reason,
                        "strategy": pos.get("strategy"),
                        "entry_date": pos.get("entry_date"),
                        "days_held": int(pos.get("days_held", 0)),
                    }
                    trades.append(trade_record)
                    record_dashboard_event(
                        "SELL", name, pos["code"], exit_reason,
                        shares=pos["shares"], price=round(actual_exit_price, 2),
                        net_return_pct=round(net_ret_pct, 2),
                    )
                    dashboard_dirty = True
                    dashboard_critical = True
                    closed_positions.append(name)
                    
            for name in closed_positions:
                del positions[name]

            # 보유일수와 트레일링 상태도 매 루프 원자적으로 보존한다.
            save_json(POSITIONS_FILE, positions)
                
            if closed_positions:
                save_json(POSITIONS_FILE, positions)
                save_json(TRADES_FILE, trades)
                if git_ok and not git_commit_and_push(f"매도 체결: {', '.join(closed_positions)}"):
                    trading_halted = True
                    risk_reasons.append("매도 체결 내역 GitHub push 실패")
                
            # 3. 신규 매수 신호 탐색 및 진입 집행
            if (
                DYNAMIC_SCREENING and STOCK_STRATEGIES_ENABLED
                and last_screened_date != today_str
            ):
                trading_halted = True
                risk_reasons.append("오늘자 D-1 유니버스 없음")
                print(f"[{now_str}] [RISK] 오늘자 D-1 유니버스가 없어 신규 주문을 중단합니다.")

            if not is_entry_window(now) and not is_mim_entry_window(now):
                print(f"[{now_str}] [ENTRY] 신규 진입 허용 시간({ENTRY_START_TIME}~{ENTRY_END_TIME}) 외")

            loop_halted = trading_halted or account_lookup_failed
            entry_window_open = is_entry_window(now) or (
                MIM_ENABLED and is_mim_entry_window(now)
            )
            if not loop_halted and entry_window_open and len(positions) < MAX_POSITIONS:
                if is_mim_entry_window(now):
                    signals = [
                        sig for sig in raw_signals
                        if sig.get("strategy") == MIM_STRATEGY
                    ]
                else:
                    signals = [
                        sig for sig in raw_signals
                        if sig.get("strategy") != MIM_STRATEGY
                    ]
                if stock_block_reason:
                    signals = [
                        sig for sig in signals
                        if sig.get("strategy") in NON_STOCK_STRATEGIES
                    ]
                # 재기동 직후의 일시적/이미 끝난 신호 추격을 막는다. 동일 전략 신호가
                # 30초 간격으로 3회 연속 유지될 때만 신규 주문 후보로 인정한다.
                signals, signal_confirmations = confirm_persistent_signals(
                    signals, signal_confirmations, required=3
                )
                dashboard_context.update({
                    "raw_signal_count": len(raw_signals),
                    "confirming_signal_count": sum(
                        1 for count in signal_confirmations.values() if count < 3
                    ),
                    "confirmed_signal_count": len(signals),
                    "signal_progress": [
                        {"name": sig["name"], "strategy": sig["strategy"],
                         "count": signal_confirmations.get(
                             (sig["name"], sig["strategy"]), 0
                         )}
                        for sig in raw_signals
                    ],
                    "signal_statuses": [
                        {
                            "name": name,
                            "active": name in raw_signal_by_name,
                            "strategy": raw_signal_by_name.get(name, {}).get("strategy"),
                            "size_multiplier": raw_signal_by_name.get(name, {}).get(
                                "size_multiplier", 1.0
                            ),
                            "count": signal_confirmations.get(
                                (name, raw_signal_by_name.get(name, {}).get("strategy")), 0
                            ) if name in raw_signal_by_name else 0,
                        }
                        for name in active_universe
                    ],
                })
                # 주문 가격도 키움 실시간 체결가로 통일한다. 오래된 시세는 후보에서 제외한다.
                live_signals = []
                for sig in signals:
                    signal_code = sig.get("code") or active_universe.get(sig["name"], "")
                    quote = realtime.quote(signal_code)
                    if quote:
                        sig = dict(sig)
                        sig["entry_price"] = quote["price"]
                        live_signals.append(sig)
                    else:
                        record_dashboard_event(
                            "SKIP", sig["name"], active_universe.get(sig["name"], ""),
                            "키움 실시간 시세 없음",
                        )
                        dashboard_dirty = True
                signals = live_signals
                # 계좌 스냅샷의 현금을 재사용하고, 실제 주문 후보가 있을 때만
                # kt00001을 강제 갱신해 30초 루프의 중복 호출을 피한다.
                cash = float(account.get("cash") or 0)
                if signals:
                    cash = api.get_cash_balance(force_refresh=True)
                total_assets = float(account.get("total_assets") or (
                    cash + sum(p["shares"] * p["buy_price"] for p in positions.values())
                ))
                
                new_buys = []
                for sig in signals:
                    name = sig["name"]
                    if name in positions:
                        record_dashboard_event(
                            "SKIP", name, active_universe.get(name, ""), "이미 보유 중"
                        )
                        dashboard_dirty = True
                        continue
                    if (
                        sig.get("strategy") in {
                            "비우호장 강한 VBO (15%)", INTRADAY_SURGE_STRATEGY
                        }
                        and any(
                            pos.get("strategy") == sig.get("strategy")
                            for pos in positions.values()
                        )
                    ):
                        record_dashboard_event(
                            "SKIP", name, active_universe.get(name, ""),
                            f"{sig.get('strategy')} 1종목 보유 상한",
                        )
                        dashboard_dirty = True
                        continue
                    if len(positions) >= MAX_POSITIONS:
                        record_dashboard_event(
                            "SKIP", name, active_universe.get(name, ""),
                            "최대 보유 종목 수 도달",
                        )
                        dashboard_dirty = True
                        continue
                    code = sig.get("code") or active_universe[name]
                    candidate_sector = universe_metadata.get(name, {}).get(
                        "sector", "미분류"
                    )
                    if sector_limit_reached(
                        candidate_sector, positions, universe_metadata
                    ):
                        reason = f"동일 업종 보유 상한({MAX_POSITIONS_PER_SECTOR}종목)"
                        print(f"[{now_str}] [SKIP] {name}: {reason}")
                        record_dashboard_event("SKIP", name, code, reason)
                        dashboard_dirty = True
                        continue
                    entry_p = sig["entry_price"]
                    stop_loss_pct = float(sig.get("stop_loss_pct", STOP_LOSS_PCT))
                    spendable_cash = cash + parking_liquidation_value(
                        parking, parking_price
                    )
                    shares = calculate_entry_shares(
                        total_assets, entry_cash_after_reserve(total_assets, spendable_cash),
                        entry_p, stop_loss_pct,
                        entry_size_multiplier * float(sig.get("size_multiplier", 1.0)),
                        sig.get("risk_pct"),
                        max(
                            float(sig.get("max_investment_pct") or 0),
                            MAX_ALLOC_PER_STOCK,
                        ),
                    )
                    shares = min(shares, int(sig.get("max_shares", shares)))
                    if sig.get("max_investment_pct") is not None:
                        strategy_allocation_shares = int(
                            total_assets * float(sig["max_investment_pct"]) // entry_p
                        )
                        if (
                            sig.get("allow_one_share_over_allocation")
                            and shares >= 1
                        ):
                            strategy_allocation_shares = max(
                                1, strategy_allocation_shares
                            )
                        shares = min(shares, strategy_allocation_shares)
                    if shares < 1:
                        print(f"[{now_str}] [SKIP] {name}: 1주가 투자/위험 한도를 초과")
                        record_dashboard_event(
                            "SKIP", name, code, "1주가 투자/위험 한도를 초과"
                        )
                        dashboard_dirty = True
                        continue
                        
                    invest = shares * entry_p
                    # 현금이 모자라면 대기자금 ETF를 필요한 만큼만 먼저 판다.
                    shortfall = invest - entry_cash_after_reserve(total_assets, cash)
                    sell_qty = parking_sell_shares_needed(
                        shortfall, parking_price, parking_shares(parking)
                    )
                    if sell_qty > 0:
                        sold_qty, sold_price, sell_error = execute_parking_order(
                            api, "sell", sell_qty, parking_price
                        )
                        if sold_qty > 0:
                            parking = apply_parking_fill(
                                parking, "sell", sold_qty, sold_price, today_str
                            )
                            save_json(PARKING_STATE_FILE, parking)
                            record_dashboard_event(
                                "PARK", PARKING_ETF_NAME, PARKING_ETF_CODE,
                                f"{name} 매수 자금 회수",
                            )
                            dashboard_dirty = True
                        if sell_error:
                            trading_halted = True
                            failure_reason = f"대기자금 회수 실패: {sell_error}"
                            risk_reasons.append(failure_reason)
                            record_dashboard_event(
                                "RISK", PARKING_ETF_NAME, PARKING_ETF_CODE,
                                failure_reason,
                            )
                            dashboard_dirty = True
                            dashboard_critical = True
                            print(f"[{now_str}] [CRITICAL] {failure_reason}; 신규주문 중단")
                            break
                        cash = api.get_cash_balance(force_refresh=True)
                    if cash >= invest:
                        before_qty = 0
                        if account.get("ok"):
                            before_qty = int(account["positions"].get(code, {}).get("shares", 0))
                        order = api.send_buy_order(
                            code, name, shares, entry_p,
                            exchange=sig.get("order_exchange", "SOR"),
                        )
                        if order.get("status") == "filled":
                            filled_qty = shares
                            fill_price = entry_p
                        elif order.get("status") == "accepted":
                            fill = confirm_order_fill(
                                api, order.get("order_id"), code, shares
                            )
                            filled_qty = int(fill.get("filled_qty", 0))
                            fill_price = float(fill.get("fill_price", 0))
                        else:
                            filled_qty = 0
                            fill_price = 0
                        if filled_qty <= 0:
                            trading_halted = True
                            failure_detail = (
                                order.get("message")
                                if order.get("status") == "error"
                                else fill.get("error", "체결 확인 시간 초과")
                                if order.get("status") == "accepted"
                                else "알 수 없는 주문 상태"
                            )
                            failure_reason = f"{name} 매수 실패: {failure_detail}"
                            risk_reasons.append(failure_reason)
                            record_dashboard_event(
                                "RISK", name, code, failure_reason
                            )
                            dashboard_dirty = True
                            dashboard_critical = True
                            print(
                                f"[{now_str}] [CRITICAL] {failure_reason}; "
                                "장부 미반영 및 신규주문 중단"
                            )
                            break
                        positions[name] = {
                            "code": code,
                            "sector": candidate_sector,
                            "shares": filled_qty,
                            "buy_price": fill_price,
                            "stop_loss_pct": stop_loss_pct,
                            "take_profit_pct": sig.get("take_profit_pct", TAKE_PROFIT_PCT),
                            "days_held": 1,
                            "entry_date": today_str,
                            "last_held_date": today_str,
                            "trailing_active": False,
                            "highest_price": fill_price,
                            "trailing_gap_pct": max(0.02, min(0.04,
                                sig.get("atr", fill_price * 0.03) / max(fill_price, 1))),
                            "strategy": sig["strategy"],
                            "roundtrip_cost_pct": sig.get(
                                "roundtrip_cost_pct", TOTAL_ROUNDTRIP_COST_PCT
                            ),
                            "order_exchange": sig.get("order_exchange", "SOR"),
                        }
                        if sig["strategy"] == MIM_STRATEGY:
                            mim_state["entry_attempted"] = True
                            save_json(MIM_STATE_FILE, mim_state)
                        realtime.reset_high_water(code, fill_price)
                        cash -= filled_qty * fill_price
                        trade_record = {
                            "timestamp": now_str,
                            "type": "BUY",
                            "name": name,
                            "code": code,
                            "shares": filled_qty,
                            "entry_price": fill_price,
                            "signal_price": float(entry_p),  # 주문 직전 실시간가(슬리피지 측정용)
                            "stop_loss_pct": sig.get("stop_loss_pct", STOP_LOSS_PCT),
                            "take_profit_pct": sig.get("take_profit_pct", TAKE_PROFIT_PCT),
                            "strategy": sig["strategy"],
                            "roundtrip_cost_pct": sig.get(
                                "roundtrip_cost_pct", TOTAL_ROUNDTRIP_COST_PCT
                            ),
                        }
                        trades.append(trade_record)
                        record_dashboard_event(
                            "BUY", name, code, sig["strategy"],
                            shares=filled_qty, price=round(fill_price, 2),
                        )
                        dashboard_dirty = True
                        dashboard_critical = True
                        new_buys.append(name)
                        
                if new_buys:
                    save_json(POSITIONS_FILE, positions)
                    save_json(TRADES_FILE, trades)
                    if git_ok and not git_commit_and_push(f"매수 저격 체결: {', '.join(new_buys)}"):
                        trading_halted = True
                        risk_reasons.append("매수 체결 내역 GitHub push 실패")
            else:
                # 진입 판단을 건너뛴 루프는 연속 확인으로 인정하지 않는다.
                signal_confirmations = {}
                for item in dashboard_context.get("signal_statuses", []):
                    item["count"] = 0
                    
            # 코어 자산배분: 월 1회(최초 실행 포함) 목표 비중으로 맞춘다. 매도 → 매수 순서.
            if (
                CORE_ALLOCATION_ENABLED
                and is_core_rebalance_window(now)
                and core_rebalance_due(core_state, now)
                and account.get("ok")
                and not (trading_halted or account_lookup_failed)
            ):
                core_prices = {
                    code: (realtime.quote(code) or {}).get("price")
                    for code in set(CORE_TARGETS) | set(core_holdings(core_state))
                }
                core_total_assets = float(account.get("total_assets") or 0)
                core_plan = plan_core_rebalance(core_total_assets, core_prices, core_state)
                if core_plan is None:
                    print(f"[{now_str}] [CORE] 실시간 시세가 없는 종목이 있어 다음 루프에 다시 시도")
                else:
                    core_failed = False
                    core_cash = api.get_cash_balance(force_refresh=True)
                    for side, code, name, qty, price in core_plan:
                        if side == "buy":
                            if entry_size_multiplier < 1:
                                continue  # 계좌 낙폭 구간에서는 코어 비중을 늘리지 않는다
                            spendable = entry_cash_after_reserve(
                                core_total_assets,
                                core_cash + parking_liquidation_value(parking, parking_price),
                            )
                            qty = min(qty, int(spendable // price))
                            if qty < 1:
                                continue
                            shortfall = qty * price - entry_cash_after_reserve(
                                core_total_assets, core_cash
                            )
                            sell_qty = parking_sell_shares_needed(
                                shortfall, parking_price, parking_shares(parking)
                            )
                            if sell_qty > 0:
                                sold_qty, sold_price, sell_error = execute_parking_order(
                                    api, "sell", sell_qty, parking_price
                                )
                                if sold_qty > 0:
                                    parking = apply_parking_fill(
                                        parking, "sell", sold_qty, sold_price, today_str
                                    )
                                    save_json(PARKING_STATE_FILE, parking)
                                if sell_error:
                                    core_failed = True
                                    failure_reason = f"대기자금 회수 실패: {sell_error}"
                                    break
                                core_cash = api.get_cash_balance(force_refresh=True)
                                qty = min(qty, int(entry_cash_after_reserve(
                                    core_total_assets, core_cash
                                ) // price))
                                if qty < 1:
                                    continue
                        filled_qty, fill_price, order_error = execute_etf_order(
                            api, side, code, f"{name} 코어", qty, price,
                            "코어 자산배분 리밸런싱",
                        )
                        if filled_qty > 0:
                            core_state = apply_core_fill(
                                core_state, side, code, filled_qty, fill_price
                            )
                            save_json(CORE_STATE_FILE, core_state)
                            record_dashboard_event(
                                "CORE", name, code,
                                "코어 비중 축소" if side == "sell" else "코어 비중 확대",
                            )
                            dashboard_dirty = True
                            core_cash = api.get_cash_balance(force_refresh=True)
                        if order_error:
                            core_failed = True
                            failure_reason = f"코어 자산배분 {name} 체결 미확인: {order_error}"
                            break
                    if core_failed:
                        trading_halted = True
                        risk_reasons.append(failure_reason)
                        record_dashboard_event("RISK", reason=failure_reason)
                        dashboard_dirty = True
                        dashboard_critical = True
                        print(f"[{now_str}] [CRITICAL] {failure_reason}; 신규주문 중단")
                    else:
                        core_state["last_rebalance_month"] = now.strftime("%Y-%m")
                        core_state.pop("flow_rebalance_pending", None)
                        save_json(CORE_STATE_FILE, core_state)
                        print(f"[{now_str}] [CORE] 이번 달 코어 자산배분 리밸런싱 완료")

            # 장 막판(진입 종료 후)에 남는 현금을 하루 한 번 대기자금 ETF에 넣는다.
            if (
                PARKING_ENABLED
                and is_parking_window(now)
                and parking.get("last_park_date") != today_str
                and not (trading_halted or account_lookup_failed)
                and realtime.quote(PARKING_ETF_CODE)
            ):
                parking["last_park_date"] = today_str
                park_price = float(realtime.quote(PARKING_ETF_CODE)["price"])
                park_cash = api.get_cash_balance(force_refresh=True)
                park_total_assets = float(account.get("total_assets") or 0)
                park_qty = parking_buy_shares(park_total_assets, park_cash, park_price)
                if park_qty > 0:
                    bought_qty, bought_price, buy_error = execute_parking_order(
                        api, "buy", park_qty, park_price
                    )
                    if bought_qty > 0:
                        parking = apply_parking_fill(
                            parking, "buy", bought_qty, bought_price, today_str
                        )
                        record_dashboard_event(
                            "PARK", PARKING_ETF_NAME, PARKING_ETF_CODE,
                            "남는 현금 대기자금 편입",
                        )
                        dashboard_dirty = True
                    if buy_error:
                        trading_halted = True
                        failure_reason = f"대기자금 편입 체결 미확인: {buy_error}"
                        risk_reasons.append(failure_reason)
                        record_dashboard_event(
                            "RISK", PARKING_ETF_NAME, PARKING_ETF_CODE, failure_reason,
                        )
                        dashboard_dirty = True
                        dashboard_critical = True
                        print(f"[{now_str}] [CRITICAL] {failure_reason}")
                save_json(PARKING_STATE_FILE, parking)

            # 코어·대기자금·추세 ETF 주문은 대시보드 정보 계산 이후에 실행되므로,
            # 같은 루프의 게시에 체결 결과가 반영되도록 다시 계산한다.
            held_strategies = {
                pos.get("strategy") for pos in load_json(POSITIONS_FILE, {}).values()
            }
            if dashboard_context.get("etf_rotation"):
                dashboard_context["etf_rotation"]["held"] = (
                    ETF_ROTATION_STRATEGY in held_strategies
                )
            if dashboard_context.get("sector_etf"):
                dashboard_context["sector_etf"]["held"] = (
                    SECTOR_ETF_STRATEGY in held_strategies
                )
            if account.get("ok"):
                refreshed_total_assets = float(account.get("total_assets") or 0)
                dashboard_context["position_weights"] = position_weight_pcts(
                    load_json(POSITIONS_FILE, {}), realtime, refreshed_total_assets,
                )
                display_core_prices = {
                    code: (realtime.quote(code, DISPLAY_QUOTE_MAX_AGE_SECONDS) or {}).get("price")
                    for code in CORE_TARGETS
                }
                # 재시작 직후 실시간 캐시가 비어도 마지막 확인가로 표시하도록 장부에 남긴다.
                if remember_core_last_prices(core_state, display_core_prices):
                    save_json(CORE_STATE_FILE, core_state)
                dashboard_context["core"] = core_dashboard_info(
                    core_state, display_core_prices, refreshed_total_assets,
                )
                dashboard_context["parking"] = parking_dashboard_info(
                    parking,
                    parking_reference_price(parking, realtime.quote(PARKING_ETF_CODE, DISPLAY_QUOTE_MAX_AGE_SECONDS)),
                    refreshed_total_assets,
                )

            # 주문 처리 중 새 영구 안전잠금이 발생했을 수 있으므로 최종 상태를 다시 계산한다.
            loop_halted = trading_halted or account_lookup_failed
            signal_snapshot = (
                tuple(market_regimes.items()),
                tuple(
                    (item.get("name"), item.get("active"), item.get("strategy"),
                     item.get("count", 0))
                    for item in dashboard_context.get("signal_statuses", [])
                ),
            )
            if signal_snapshot != last_signal_snapshot:
                dashboard_dirty = True
                dashboard_critical = True
                last_signal_snapshot = signal_snapshot
            print(f"[{now_str}] 감시 완료. 현재 보유 종목: {list(positions.keys())}")
            status, detail = build_operational_status(
                loop_halted, risk_reasons, len(positions)
            )
            dashboard_context.update({
                "last_loop_at": now_str,
                "entry_allowed": (
                    not loop_halted
                    and (is_entry_window(now) or (
                        MIM_ENABLED and is_mim_entry_window(now)
                    ))
                    and len(positions) < MAX_POSITIONS
                ),
            })
            update_health_status(status=status, detail=detail)

            # 분석 원본은 GitHub에 올리지 않고 로컬 SQLite에 5분 단위로 누적한다.
            try:
                research_quotes = {}
                for research_pos in positions.values():
                    research_quote = realtime.quote(research_pos.get("code", ""))
                    if research_quote:
                        research_quotes[research_pos["code"]] = research_quote.get("price")
                record_research_snapshot(
                    RESEARCH_DB_FILE, now, status, detail, CURRENT_MARKET_OPEN,
                    dashboard_context, positions, research_quotes, trades,
                )
            except Exception as research_error:
                print(f"[RESEARCH] 로컬 분석 데이터 저장 실패: {research_error}")

            # 정상↔위험 상태가 바뀌면 30분 주기를 기다리지 않고 즉시 대시보드에 공개한다.
            current_state = (status, detail)
            if git_ok and current_state != last_published_state:
                event_type = "ERROR" if status == "error" else "RISK" if status == "halted" else "SYSTEM"
                record_dashboard_event(event_type, reason=detail)
                dashboard_dirty = True
                dashboard_critical = True
                last_published_state = current_state

            dashboard_due = is_dashboard_five_minute_due(
                last_dashboard_push_time, now
            )
            dashboard_heartbeat_due = is_dashboard_heartbeat_due(
                last_dashboard_push_time
            )
            if git_ok and (dashboard_critical or (dashboard_dirty and dashboard_due)
                           or (positions and dashboard_due)
                           or dashboard_heartbeat_due):
                if sync_dashboard_to_branch(
                    status, detail, positions, realtime, dashboard_context
                ):
                    dashboard_dirty = False
                    dashboard_critical = False
                    last_dashboard_push_time = time.time()
                
            time.sleep(POLL_INTERVAL_SECONDS)
            
        except Exception as e:
            print(f"[ERROR] 루프 실행 중 예외 발생: {e}")
            error_detail = f"루프 예외: {type(e).__name__}: {e}"
            update_health_status(status="error", detail=error_detail)
            record_dashboard_event("ERROR", reason=error_detail)
            sync_dashboard_to_branch(
                "error", error_detail, load_json(POSITIONS_FILE, {}), realtime,
                dashboard_context,
            )
            last_dashboard_push_time = time.time()
            dashboard_dirty = False
            dashboard_critical = False
            last_published_state = ("error", error_detail)
            time.sleep(POLL_INTERVAL_SECONDS)

if __name__ == "__main__":
    _instance_lock = acquire_instance_lock()
    run_trading_loop()

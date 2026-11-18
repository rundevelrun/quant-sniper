import os
import time
import json
import datetime
import subprocess
import fcntl
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
    MIN_STOP_LOSS_PCT, MAX_STOP_LOSS_PCT
)
from strategy import calculate_atr, evaluate_signals, is_favorable_market
from kiwoom_api import KiwoomRestAPI
from kiwoom_realtime import KiwoomRealtimeFeed

TRADES_FILE = os.path.join(BASE_DIR, "trades.json")
POSITIONS_FILE = os.path.join(BASE_DIR, "positions.json")
HEALTH_FILE = os.path.join(BASE_DIR, "health.json")
UNIVERSE_FILE = os.path.join(BASE_DIR, "universe.json")
RISK_FILE = os.path.join(BASE_DIR, "risk_state.json")
DASHBOARD_FILE = os.path.join(BASE_DIR, "DASHBOARD.md")
DASHBOARD_EVENTS_FILE = os.path.join(BASE_DIR, "dashboard_events.json")
DASHBOARD_STATE_FILE = os.path.join(BASE_DIR, "dashboard_state.json")
LOCK_FILE = os.path.join(BASE_DIR, ".quant_sniper.lock")
CURRENT_MARKET_OPEN = False

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

def dashboard_status_label(status):
    """운영 상태를 GitHub 대시보드용 신호등 레이블로 변환한다."""
    if status == "running":
        return "🟢 정상"
    if status == "halted":
        return "🔴 거래 중단"
    if status == "error":
        return "🔴 오류"
    return f"🟡 {_md(status)}"

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
            else "🔴 비우호 국면 — VBO 차단·강한 RSI만 50%"
        )
    lines = [
        "# Quant Sniper Dashboard", "",
        f"> 마지막 갱신: **{now.strftime('%Y-%m-%d %H:%M:%S')} KST**", "",
        "## 1. 실시간 현재 상태 — 기간 제한 없음", "",
        "| 시스템 상태 | 장 운영 | 상세 | 보유 종목 |",
        "|---|---:|---|---:|",
        f"| {dashboard_status_label(status)} "
        f"| {'🟢 OPEN' if CURRENT_MARKET_OPEN else '🔴 CLOSED'} "
        f"| {_md(detail)} | {len(positions)} |",
        "", "### 시스템 신선도", "",
        "| 키움 WebSocket | 마지막 실시간 시세 | 마지막 계좌 조회 | 마지막 정상 루프 | 실행 커밋 |",
        "|---|---|---|---|---|",
        f"| {'정상' if realtime_ok else '연결 대기'} | {_md(context.get('last_quote_at', '-'))} "
        f"| {_md(context.get('last_account_sync', '-'))} | {_md(context.get('last_loop_at', '-'))} "
        f"| `{_md(commit_hash)}` |",
    ]
    if not realtime_ok and realtime_error:
        lines += ["", f"> WebSocket 상태: {_md(realtime_error)}"]

    lines += [
        "", "### 오늘의 감시 및 신호", "",
        f"> 시장 국면: **{market_label}**  ",
        "> 우호장: RSI·VBO 정상 크기 · 비우호장: VBO 차단, 강한 RSI 반등만 50%",
        "",
        "| 유니버스 기준일 | 데이터 출처 | 감시 종목 | 원신호 | 연속 확인 중 | 확정 신호 |",
        "|---|---|---:|---:|---:|---:|",
        f"| {_md(context.get('universe_date', '-'))} | {_md(context.get('universe_source', '-'))} "
        f"| {int(context.get('universe_count', 0))} | {int(context.get('raw_signal_count', 0))} "
        f"| {int(context.get('confirming_signal_count', 0))} | {int(context.get('confirmed_signal_count', 0))} |",
        "", "#### 감시 종목 전체", "",
        "| 종목 | 코드 | 시장 | 업종 | 현재가 | 우호장 최대 수량 | 비우호장 최대 수량 | 현재 자금 조건 | 직전 RSI | 현재 RSI | 진입 차단 사유 | 매수 신호 |",
        "|---|---:|---|---|---:|---:|---:|---|---:|---:|---|---|",
    ]
    universe_symbols = context.get("tradeability") or context.get("universe_symbols", [])
    signal_statuses = {
        item.get("name"): item for item in context.get("signal_statuses", [])
    }
    signal_diagnostics = {
        item.get("name"): item for item in context.get("signal_diagnostics", [])
    }
    if universe_symbols:
        for item in universe_symbols:
            price = item.get("price")
            max_shares = item.get("max_shares")
            favorable_shares = item.get("favorable_max_shares", max_shares)
            unfavorable_shares = item.get("unfavorable_max_shares", max_shares)
            eligibility = (
                (
                    "가능 (비우호장 50%)"
                    if item.get("eligible") and float(item.get("market_size_multiplier", 1.0)) < 1
                    else "가능"
                    if item.get("eligible")
                    else _md(item.get("reason", "불가"))
                )
                if "eligible" in item else "계산 대기"
            )
            signal = signal_statuses.get(item.get("name"), {})
            diagnostic = signal_diagnostics.get(item.get("name"), {})
            previous_rsi = diagnostic.get("previous_rsi")
            current_rsi = diagnostic.get("rsi")
            blockers = (
                f"RSI: {_md(diagnostic.get('rsi_blocker', '계산 대기'))} · "
                f"VBO: {_md(diagnostic.get('vbo_blocker', '계산 대기'))}"
            )
            signal_text = (
                f"🟢 {_md(signal.get('strategy'))}"
                f"{' · 비우호장 50%' if float(signal.get('size_multiplier', 1.0)) < 1 else ''} "
                f"({int(signal.get('count', 0))}/3)"
                if signal.get("active") else "🔴 신호 없음"
            )
            lines.append(
                f"| {_md(item.get('name'))} | `{_md(item.get('code'))}` "
                f"| {_md(item.get('market', '미분류'))} "
                f"| {_md(item.get('sector', '미분류'))} "
                f"| {f'{float(price):,.0f}원' if price else '-'} "
                f"| {int(favorable_shares) if favorable_shares is not None else '-'} "
                f"| {int(unfavorable_shares) if unfavorable_shares is not None else '-'} "
                f"| {eligibility} "
                f"| {f'{float(previous_rsi):.1f}' if previous_rsi is not None else '-'} "
                f"| {f'{float(current_rsi):.1f}' if current_rsi is not None else '-'} "
                f"| {blockers} | {signal_text} |"
            )
    else:
        lines.append("| - | - | - | - | - | - | - | - | - | - | - | - |")
    drawdown = context.get("account_drawdown_pct")
    daily_return = context.get("daily_return_pct")
    lines += [
        "", "### 위험 관리 현황", "",
        "| 신규 매수 | 계좌 고점 대비 낙폭 | 오늘 수익률 | 주문 배율 | 거래당 위험 | 종목당 한도 | 남은 슬롯 |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| {'가능' if context.get('entry_allowed', False) else '중단'} "
        f"| {f'{drawdown:.2f}%' if drawdown is not None else '-'} "
        f"| {f'{daily_return:+.2f}%' if daily_return is not None else '-'} "
        f"| {float(context.get('entry_size_multiplier', 1.0))*100:.0f}% "
        f"| 0.7% | {MAX_ALLOC_PER_STOCK*100:.0f}% | {max(0, MAX_POSITIONS-len(positions))} |",
        "", "### 현재 보유 포지션 및 청산 계획 — 전체", "",
        "| 종목 | 수량 | 매수가 | 현재가 | 수익률 | 최초 손절 | 현재 보호선 | 최고가 대비 | 타임스탑 | 추적 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    if positions:
        for name, pos in positions.items():
            quote = realtime.quote(pos.get("code", ""))
            current = float(quote.get("price", 0)) if quote else 0
            buy = float(pos.get("buy_price", 0))
            return_pct = ((current - buy) / buy * 100) if current and buy else None
            return_text = f"{return_pct:+.2f}%" if return_pct is not None else "-"
            stop_loss_pct = float(pos.get("stop_loss_pct", STOP_LOSS_PCT))
            initial_stop = buy * (1 - stop_loss_pct)
            current_stop = float(pos.get("trailing_stop_price", initial_stop))
            highest = float(pos.get("highest_price", buy))
            off_high = ((current - highest) / highest * 100) if current and highest else None
            if pos.get("trailing_active"):
                time_stop_text = "추적 중"
            elif current > buy:
                time_stop_text = f"{max(0, MAX_PROFIT_HOLD_DAYS-int(pos.get('days_held', 0)))}일"
            else:
                time_stop_text = f"{max(0, MAX_HOLD_DAYS-int(pos.get('days_held', 0)))}일"
            lines.append(
                f"| {_md(name)} | {int(pos.get('shares', 0))} | {buy:,.0f} | {current:,.0f} "
                f"| {return_text} | {initial_stop:,.0f} | {current_stop:,.0f} "
                f"| {f'{off_high:+.2f}%' if off_high is not None else '-'} "
                f"| {time_stop_text} | {'ON' if pos.get('trailing_active') else 'OFF'} |"
            )
    else:
        lines.append("| 보유 종목 없음 | - | - | - | - | - | - | - | - | - |")

    lines += [
        "", "## 2. 최근 3일 활동", "",
        "| 매수 | 매도 | 스킵 | 위험 | 오류 |",
        "|---:|---:|---:|---:|---:|",
        f"| {counts['BUY']} | {counts['SELL']} | {counts['SKIP']} | {counts['RISK']} | {counts['ERROR']} |",
        "", "### 최근 3일 청산 성과", "",
        "| 청산 거래 | 승률 | 평균 순수익률 |",
        "|---:|---:|---:|",
        f"| {len(returns)} | {f'{win_rate:.1f}%' if win_rate is not None else '-'} "
        f"| {f'{avg_return:+.2f}%' if avg_return is not None else '-'} |",
        "", "### 최근 3일 이벤트", "",
        "| 시각 | 유형 | 종목 | 수량 | 가격 | 사유 | 반복 |",
        "|---|---|---|---:|---:|---|---:|",
    ]
    visible_types = {"BUY", "SELL", "SKIP", "RISK", "ERROR"}
    visible = [e for e in recent_events if e.get("type") in visible_types]
    for event in reversed(visible[-100:]):
        lines.append(
            f"| {_md(event.get('timestamp', '')).replace('T', ' ')} | {_md(event.get('type'))} "
            f"| {_md(event.get('name') or event.get('code') or '-')} "
            f"| {_md(event.get('shares', '-'))} | {_md(event.get('price', '-'))} "
            f"| {_md(event.get('reason', '-'))} | {int(event.get('count', 1))} |"
        )
    if not visible:
        lines.append("| - | - | 기록 없음 | - | - | - | - |")
    lines += [
        "", "## 3. 전체 누적 성과 — 전체 거래 이력", "",
        "| 청산 거래 | 승률 | 누적 순손익 | 평균 이익률 | 평균 손실률 | Profit Factor | 최대 연속 손실 | 누적 비용 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
        f"| {len(all_returns)} "
        f"| {f'{all_win_rate:.1f}%' if all_win_rate is not None else '-'} "
        f"| {total_net_profit:+,.0f}원 "
        f"| {f'{average_win:+.2f}%' if average_win is not None else '-'} "
        f"| {f'{average_loss:+.2f}%' if average_loss is not None else '-'} "
        f"| {f'{profit_factor:.2f}' if profit_factor is not None else '-'} "
        f"| {max_loss_streak}회 | {total_cost:,.0f}원 |",
    ]
    lines += ["", "> 인증정보와 민감한 계좌정보는 기록하지 않습니다.", ""]
    return "\n".join(lines)

def sync_dashboard_to_branch(status, detail, positions, realtime, context=None):
    """대시보드 파일만 부모 없는 단일 커밋으로 origin/dashboard에 교체한다."""
    if not AUTO_GIT_PUSH:
        return False
    events = load_json(DASHBOARD_EVENTS_FILE, [])
    render_context = dict(context or {})
    render_context["all_trades"] = load_json(TRADES_FILE, [])
    now = datetime.datetime.now()
    realtime_ok, realtime_error = realtime.is_healthy()
    state_positions = []
    for name, pos in positions.items():
        quote = realtime.quote(pos.get("code", ""))
        current_price = float(quote.get("price", 0)) if quote else None
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
    dashboard_state = {
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
        "account_drawdown_pct": render_context.get("account_drawdown_pct"),
        "daily_return_pct": render_context.get("daily_return_pct"),
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
    }
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
            ["git", "hash-object", "-w", "DASHBOARD.md"], cwd=BASE_DIR,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        events_blob = subprocess.run(
            ["git", "hash-object", "-w", "dashboard_events.json"], cwd=BASE_DIR,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        state_blob = subprocess.run(
            ["git", "hash-object", "-w", "dashboard_state.json"], cwd=BASE_DIR,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        tree_input = (
            f"100644 blob {dashboard_blob}\tREADME.md\n"
            f"100644 blob {events_blob}\tdashboard_events.json\n"
            f"100644 blob {state_blob}\tdashboard_state.json\n"
        )
        tree = subprocess.run(
            ["git", "mktree"], cwd=BASE_DIR, input=tree_input,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        commit = subprocess.run(
            ["git", "commit-tree", tree, "-m", "dashboard: rolling 7-day snapshot"],
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

    if highest_price >= buy_price * (1 + TAKE_PROFIT_PCT):
        gap = float(pos.get("trailing_gap_pct", max(0.02, min(0.04, stop_loss_pct / 1.5))))
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

def get_kiwoom_candles(api, realtime, cache, code, now, count=70):
    """일봉은 일 1회 REST로 받고 당일 봉만 키움 실시간 체결로 갱신한다."""
    date_key = now.strftime("%Y%m%d")
    cached = cache.get(code)
    if not cached or cached["date"] != date_key:
        candles = api.get_daily_candles(code, count)
        if not candles:
            return []
        cache[code] = {"date": date_key, "candles": candles}
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
                           size_multiplier=1.0):
    """종목당 투자 한도와 거래당 계획 손실 0.7%를 모두 지키는 정수 수량을 산출한다."""
    if min(total_assets, cash, entry_price, stop_loss_pct, size_multiplier) <= 0:
        return 0
    allocation_cap = total_assets * MAX_ALLOC_PER_STOCK * size_multiplier
    risk_budget = total_assets * MAX_RISK_PER_TRADE_PCT * size_multiplier
    risk_per_share = entry_price * stop_loss_pct
    shares_by_allocation = int(allocation_cap // entry_price)
    shares_by_risk = int(risk_budget // risk_per_share)
    shares_by_cash = int(cash // entry_price)
    return max(0, min(shares_by_allocation, shares_by_risk, shares_by_cash))


def screening_preferred_max_price(account):
    """비우호장 50%·최대 손절폭에서도 1주 가능한 전일종가 상한을 계산한다."""
    if not account.get("ok"):
        return None
    total_assets = float(account.get("total_assets") or 0)
    cash = float(account.get("cash") or 0)
    if min(total_assets, cash) <= 0:
        return None
    return min(
        cash,
        total_assets * MAX_ALLOC_PER_STOCK * 0.5,
        total_assets * MAX_RISK_PER_TRADE_PCT * 0.5 / MAX_STOP_LOSS_PCT,
    )

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

def check_git_permission():
    """봇 기동 시 깃 푸시 권한 사전 점검"""
    if not AUTO_GIT_PUSH:
        print("[GIT] AUTO_GIT_PUSH=false: GitHub 연동이 비활성화되었습니다.")
        return False
    repo_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        res = subprocess.run(["git", "push", "--dry-run"], cwd=repo_dir, capture_output=True, text=True)
        if res.returncode == 0:
            print("[GIT] ✅ 깃허브 푸시 권한 정상 확인 (피클 비서 실시간 연동 준비 완료)")
            return True
        else:
            print(f"[GIT WARN] ⚠️ 깃허브 푸시 권한 없음: {res.stderr.strip() or '인증 필요'}")
            print("          (거래는 로컬에 기록되나, 깃 인증 전까지 비서 알림이 보류됩니다.)")
            return False
    except Exception as e:
        print(f"[GIT WARN] git 실행 오류: {e}")
        return False

def git_commit_and_push(commit_msg):
    """체결 내역 발생 시 깃허브 자동 푸시 (피클 비서 보고 연동)"""
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

def market_session_progress(now=None):
    now = now or datetime.datetime.now()
    market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    total = (market_close - market_open).total_seconds()
    return max(0.0, min(1.0, (now - market_open).total_seconds() / total))

def run_trading_loop():
    global CURRENT_MARKET_OPEN
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
    last_screened_date = None
    active_universe = DEFAULT_UNIVERSE
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
    try:
        running_commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=BASE_DIR,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        running_commit = "unknown"
    dashboard_context = {
        "commit_hash": running_commit,
        "entry_size_multiplier": 1.0,
    }
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
            if universe_cache.get("date") == today_str and cache_symbols:
                active_universe = universe_cache["symbols"]
                universe_metadata = cache_metadata
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
            if DYNAMIC_SCREENING and last_screened_date != today_str and screening_window:
                print(f"[{now_str}] 🔍 전일(D-1) 확정 거래대금 TOP 우량주 동적 스크리닝 가동...")
                screening_account = api.get_account_snapshot()
                preferred_max_price = screening_preferred_max_price(screening_account)
                screened = api.get_previous_day_universe(
                    SCREENER_TOP_N, MIN_MARKET_CAP_KRW, MAX_MARKET_CAP_KRW,
                    preferred_max_price, SCREENER_RANK_POOL,
                )
                if screened:
                    active_universe = screened
                    universe_metadata = api.last_universe_metadata
                    last_screened_date = today_str
                    universe_source = "kiwoom_previous_day_value_affordability_priority"
                    save_json(UNIVERSE_FILE, {
                        "date": today_str, "symbols": screened,
                        "metadata": universe_metadata,
                        "source": universe_source,
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
                and last_screened_date != today_str
                and CURRENT_MARKET_OPEN
                and is_entry_window(now)
            )
            if intraday_bootstrap:
                print(f"[{now_str}] 🔄 키움 전일 거래대금 기준 장중 유니버스 복구...")
                screening_account = api.get_account_snapshot()
                preferred_max_price = screening_preferred_max_price(screening_account)
                screened = api.get_previous_day_universe(
                    SCREENER_TOP_N, MIN_MARKET_CAP_KRW, MAX_MARKET_CAP_KRW,
                    preferred_max_price, SCREENER_RANK_POOL,
                )
                if screened:
                    active_universe = screened
                    universe_metadata = api.last_universe_metadata
                    last_screened_date = today_str
                    universe_source = "kiwoom_previous_day_value_affordability_priority"
                    save_json(UNIVERSE_FILE, {
                        "date": today_str, "symbols": screened,
                        "metadata": universe_metadata,
                        "source": universe_source,
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
                else:
                    trading_halted = True
                    risk_reasons.append("키움 전일 유니버스 복구 실패")
                    print(f"[{now_str}] [RISK] 장중 유니버스 복구 실패: 신규 주문 중단")
                    
            # 장 운영 시간 확인
            if not CURRENT_MARKET_OPEN:
                print(f"[{now_str}] 정규장 운영 시간 외입니다. 대기 중...")
                realtime_connected, _ = realtime.is_healthy()
                startup_wait_done = time.time() - dashboard_started_at >= 60
                should_publish = (
                    (dashboard_dirty and (realtime_connected or startup_wait_done))
                    or (last_dashboard_push_time > 0
                        and time.time() - last_dashboard_push_time >= 1800)
                )
                if should_publish:
                    if sync_dashboard_to_branch(
                        "running", "정규장 운영 시간 외",
                        load_json(POSITIONS_FILE, {}), realtime, dashboard_context,
                    ):
                        dashboard_dirty = False
                        last_dashboard_push_time = time.time()
                time.sleep(POLL_INTERVAL_SECONDS)
                continue
                
            positions = load_json(POSITIONS_FILE, {})
            trades = load_json(TRADES_FILE, [])

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
                risk_date = today_str
                if risk_state.get("date") == today_str:
                    opening_assets = risk_state.get("opening_assets") or None
                    peak_assets = risk_state.get("peak_assets") or peak_assets
                    trading_halted = bool(risk_state.get("halted", False))
                    if trading_halted:
                        risk_reasons.append(f"지속 중인 거래 중단: {risk_state.get('reason', 'unknown')}")
                else:
                    opening_assets = account.get("total_assets") or None
                    trading_halted = False
                    risk_state = {
                        "date": today_str, "opening_assets": opening_assets,
                        "peak_assets": peak_assets,
                        "halted": False,
                        "account_baseline": account_snapshot_baseline(account),
                    }
                    save_json(RISK_FILE, risk_state)

            if account.get("ok"):
                dashboard_context["last_account_sync"] = now_str

            if account.get("ok") and opening_assets:
                current_assets = account.get("total_assets", opening_assets)
                external_flow = estimate_external_cash_flow(
                    risk_state.get("account_baseline"), account
                )
                if external_flow:
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
                save_json(RISK_FILE, risk_state)
                daily_return = (
                    (current_assets - opening_assets) / opening_assets
                    if opening_assets > 0 else 0.0
                )
                dashboard_context.update({
                    "account_drawdown_pct": account_drawdown * 100,
                    "daily_return_pct": daily_return * 100,
                    "entry_size_multiplier": entry_size_multiplier,
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
                if broker_codes != local_codes:
                    trading_halted = True
                    risk_reasons.append("계좌/로컬 포지션 불일치")
                    print(f"[{now_str}] [RISK] 계좌/로컬 포지션 불일치로 신규 주문 중단 "
                          f"(broker={sorted(broker_codes)}, local={sorted(local_codes)})")
            
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
            market_regimes = {
                "KOSPI": is_favorable_market(kospi_market_candles),
                "KOSDAQ": is_favorable_market(kosdaq_market_candles),
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
            raw_signals.sort(key=lambda item: item["score"])
            market_favorable = any(market_regimes.values())
            raw_signal_by_name = {sig["name"]: sig for sig in raw_signals}
            dashboard_context.update({
                "market_favorable": market_favorable,
                "market_regimes": market_regimes,
                "raw_signal_count": len(raw_signals),
                "signal_diagnostics": [
                    {"name": name, **signal_diagnostics.get(name, {})}
                    for name in active_universe
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

            if account.get("ok"):
                dashboard_context["tradeability"] = build_tradeability(
                    active_universe, universe_metadata, candles_dict,
                    float(account.get("total_assets") or 0),
                    float(account.get("cash") or 0), entry_size_multiplier,
                    market_regimes,
                )
                
            # 2. 보유 포지션 감시 및 청산 체크 (동적 손절, 트레일링 스탑, 익절)
            closed_positions = []
            for name, pos in list(positions.items()):
                if name not in candles_dict:
                    continue
                quote = realtime.quote(pos["code"])
                if not quote:
                    risk_reasons.append(f"{name} 실시간 시세 없음")
                    print(f"[{now_str}] [RISK] {name} 실시간 시세가 없어 청산 판단을 보류합니다.")
                    continue
                trading_days_held(pos, today_str)
                buy_p = float(pos["buy_price"])
                curr_p = quote["price"]
                exit_price = None
                exit_reason = None
                stop_price, stop_reason = update_position_stop(
                    pos, quote.get("high_water", curr_p)
                )
                if curr_p <= stop_price:
                    exit_price = stop_price
                    exit_reason = stop_reason
                else:
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
                    order = api.send_sell_order(pos["code"], name, before_qty, exit_price, exit_reason)
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
                    cost_krw = pos["shares"] * actual_exit_price * TOTAL_ROUNDTRIP_COST_PCT
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
                        "price_source": "broker_execution",
                        "gross_return_pct": round(gross_ret_pct, 2),
                        "net_return_pct": round(net_ret_pct, 2),
                        "cost_fee_krw": round(cost_krw, 0),
                        "net_profit_krw": round(net_profit_krw, 0),
                        "reason": exit_reason
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
            if DYNAMIC_SCREENING and last_screened_date != today_str:
                trading_halted = True
                risk_reasons.append("오늘자 D-1 유니버스 없음")
                print(f"[{now_str}] [RISK] 오늘자 D-1 유니버스가 없어 신규 주문을 중단합니다.")

            if not is_entry_window(now):
                print(f"[{now_str}] [ENTRY] 신규 진입 허용 시간({ENTRY_START_TIME}~{ENTRY_END_TIME}) 외")

            loop_halted = trading_halted or account_lookup_failed
            if not loop_halted and is_entry_window(now) and len(positions) < MAX_POSITIONS:
                signals = list(raw_signals)
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
                    quote = realtime.quote(active_universe.get(sig["name"], ""))
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
                    if len(positions) >= MAX_POSITIONS:
                        record_dashboard_event(
                            "SKIP", name, active_universe.get(name, ""),
                            "최대 보유 종목 수 도달",
                        )
                        dashboard_dirty = True
                        continue
                    code = active_universe[name]
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
                    shares = calculate_entry_shares(
                        total_assets, cash, entry_p, stop_loss_pct,
                        entry_size_multiplier * float(sig.get("size_multiplier", 1.0)),
                    )
                    if shares < 1:
                        print(f"[{now_str}] [SKIP] {name}: 1주가 투자/위험 한도를 초과")
                        record_dashboard_event(
                            "SKIP", name, code, "1주가 투자/위험 한도를 초과"
                        )
                        dashboard_dirty = True
                        continue
                        
                    invest = shares * entry_p
                    if cash >= invest:
                        before_qty = 0
                        if account.get("ok"):
                            before_qty = int(account["positions"].get(code, {}).get("shares", 0))
                        order = api.send_buy_order(code, name, shares, entry_p)
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
                            risk_reasons.append(f"{name} 매수 체결 미확인")
                            print(f"[{now_str}] [CRITICAL] 매수 체결 미확인: {name}; 장부 미반영 및 신규주문 중단")
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
                            "strategy": sig["strategy"]
                        }
                        realtime.reset_high_water(code, fill_price)
                        cash -= filled_qty * fill_price
                        trade_record = {
                            "timestamp": now_str,
                            "type": "BUY",
                            "name": name,
                            "code": code,
                            "shares": filled_qty,
                            "entry_price": fill_price,
                            "stop_loss_pct": sig.get("stop_loss_pct", STOP_LOSS_PCT),
                            "take_profit_pct": sig.get("take_profit_pct", TAKE_PROFIT_PCT),
                            "strategy": sig["strategy"]
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
                    not loop_halted and is_entry_window(now)
                    and len(positions) < MAX_POSITIONS
                ),
            })
            update_health_status(status=status, detail=detail)

            # 정상↔위험 상태가 바뀌면 30분 주기를 기다리지 않고 즉시 피클AI에 공개한다.
            current_state = (status, detail)
            if git_ok and current_state != last_published_state:
                event_type = "ERROR" if status == "error" else "RISK" if status == "halted" else "SYSTEM"
                record_dashboard_event(event_type, reason=detail)
                dashboard_dirty = True
                dashboard_critical = True
                last_published_state = current_state

            dashboard_due = time.time() - last_dashboard_push_time >= 300
            dashboard_heartbeat_due = time.time() - last_dashboard_push_time >= 1800
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

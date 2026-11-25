"""전체 계좌 백테스트: ETF 추세전환 + 개별주 RSI/VBO + 대기자금을 한 계좌로 시뮬레이션.

실전 코드(strategy.evaluate_signals, bot.update_position_stop, bot.time_stop_reason,
bot.calculate_entry_shares, bot.multi_asset_trend_selection, kiwoom_api 유니버스 선발)를 호출한다.

근사·한계
- 종목 목록은 현재 상장 종목뿐(상장폐지 종목 없음 → 생존편향으로 결과가 좋게 나온다)
- 과거 시가총액 = 현재 시가총액 × (과거 종가 / 현재 종가) 로 추정(증자·감자 무시)
- 유니버스: 전일 거래량 상위 300 → 시총 5천억~5조 → RSI≤28 최대 15 + VBO 준비로 30
- 개별주 진입은 14:50~15:10 → 당일 종가로 근사, 30초 3회 확인은 재현하지 않음
- ETF 진입·국면 청산은 당일 시가, 손절은 시가 갭/장중 저가 터치로 근사
- 업종 한도·일일 -4% 차단은 미반영, 계좌 낙폭 -8%/-12% 규칙은 반영
"""
import argparse
import datetime
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backtest"))

import bot  # noqa: E402
from strategy import evaluate_signals  # noqa: E402
from kiwoom_api import _mixed_universe_candidates  # noqa: E402
from config import (  # noqa: E402
    ETF_ROTATION_ASSETS, ETF_ROTATION_ALLOC_PCT, ETF_ROTATION_STOP_LOSS_PCT,
    ETF_MAX_RISK_PER_TRADE_PCT, ETF_MAX_ADVERSE_ENTRY_GAP_PCT,
    ETF_ROTATION_INVERSE_CODE, BROKER_COMMISSION_PCT, ETF_SLIPPAGE_ESTIMATE_PCT,
    SLIPPAGE_ESTIMATE_PCT, TRANSACTION_TAX_PCT, MAX_TOTAL_EXPOSURE_PCT,
    MAX_POSITIONS, MIN_MARKET_CAP_KRW, MAX_MARKET_CAP_KRW, SCREENER_TOP_N,
    SCREENER_RANK_POOL, PARKING_ETF_CODE, MARKET_PROXY_CODE,
    KOSDAQ_MARKET_PROXY_CODE,
)
from etf_rotation_backtest import load_prices, TAX_EXEMPT, DIVIDEND_TAX  # noqa: E402

CACHE = os.path.join(ROOT, "backtest", ".cache")
ETF_ONE_WAY = BROKER_COMMISSION_PCT + ETF_SLIPPAGE_ESTIMATE_PCT
STOCK_BUY = BROKER_COMMISSION_PCT + SLIPPAGE_ESTIMATE_PCT
STOCK_SELL = BROKER_COMMISSION_PCT + SLIPPAGE_ESTIMATE_PCT + TRANSACTION_TAX_PCT


def sma_rsi(close, period=14):
    """strategy.calculate_rsi와 같은 단순평균 RSI."""
    diff = close.diff()
    gain = diff.clip(lower=0).rolling(period).mean()
    loss = (-diff.clip(upper=0)).rolling(period).mean()
    rsi = 100 - 100 / (1 + gain / loss)
    return rsi.where(loss != 0, 100.0)


def load_stocks(dates):
    listing = pd.read_csv(os.path.join(CACHE, "listing.csv"), dtype={"code": str})
    listing = listing.set_index("code")
    frames = {}
    folder = os.path.join(CACHE, "stocks")
    for fn in os.listdir(folder):
        code = fn[:-4]
        if code not in listing.index:
            continue
        df = pd.read_csv(os.path.join(folder, fn), index_col=0, parse_dates=True)
        df = df[(df["Close"] > 0)]
        if len(df) < 60:
            continue
        df.loc[df["Open"] <= 0, "Open"] = df["Close"]
        df.loc[df["High"] <= 0, "High"] = df[["Open", "Close"]].max(axis=1)
        df.loc[df["Low"] <= 0, "Low"] = df[["Open", "Close"]].min(axis=1)
        frames[code] = df
    close = pd.DataFrame({c: f["Close"] for c, f in frames.items()}).reindex(dates)
    volume = pd.DataFrame({c: f["Volume"] for c, f in frames.items()}).reindex(dates)
    last_close = close.ffill().iloc[-1]
    ratio = listing.loc[close.columns, "cap_now"] / last_close
    cap = close * ratio
    rsi = close.apply(sma_rsi)
    ma20 = close.rolling(20).mean()
    ready = (close > ma20) & (ma20 > ma20.shift(1))
    return {
        "frames": frames, "listing": listing, "close": close, "volume": volume,
        "cap": cap, "rsi": rsi, "ready": ready, "value": close * volume,
    }


def candles_upto(df, d, n=70, include_today=True):
    sub = df[df.index <= d] if include_today else df[df.index < d]
    sub = sub.iloc[-n:]
    return [
        {"date": i.strftime("%Y%m%d"), "open": r.Open, "high": r.High,
         "low": r.Low, "close": r.Close, "vol": r.Volume}
        for i, r in sub.iterrows()
    ]


def run(etf_data, stocks, start, capital, use_stocks=True, use_parking=True):
    dates = etf_data["069500"].index
    dates = dates[dates >= pd.Timestamp(start)]
    etf_frames = {c: etf_data[c] for c in ETF_ROTATION_ASSETS if c in etf_data}
    park = etf_data.get(PARKING_ETF_CODE)
    cash = float(capital)
    peak = float(capital)
    positions = {}  # code -> pos
    trades = []
    curve = []
    prev_d = None

    def row_of(df, d):
        return df.loc[d] if d in df.index else None

    for d in dates:
        key = d.strftime("%Y%m%d")
        now = datetime.datetime(d.year, d.month, d.day, 9, 15)

        # ---- 평가 기준 자산(전일 종가) ----
        def equity_at(prices_fn):
            return cash + sum(p["shares"] * prices_fn(c, p) for c, p in positions.items())

        def last_price(code, pos):
            df = pos["df"]
            sub = df[df.index <= d]
            return float(sub["Close"].iloc[-1]) if len(sub) else pos["buy_price"]

        # ---- ETF 국면 ----
        completed = {c: candles_upto(f, d, 130, include_today=False) for c, f in etf_frames.items()}
        etf_pos = next((p for p in positions.values() if p["kind"] == "etf"), None)
        _, target, _ = bot.multi_asset_trend_selection(
            completed, etf_pos["code"] if etf_pos else None, now
        )
        traded_etf_today = False

        # ---- 청산 ----
        for code, pos in list(positions.items()):
            row = row_of(pos["df"], d)
            if row is None:
                continue
            bot.trading_days_held(pos, key)
            exit_price, reason = None, None
            if pos["kind"] == "etf" and target != code:
                exit_price, reason = row.Open, "ETF 국면 전환"
            else:
                stop, _ = bot.update_position_stop(pos, pos["highest_price"])
                if row.Open <= stop:
                    exit_price, reason = row.Open, "손절·보호선(시가 갭)"
                elif row.Low <= stop:
                    exit_price, reason = stop, "손절·보호선 터치"
                elif pos["kind"] == "stock":
                    ts = bot.time_stop_reason(pos, row.Open)
                    if ts:
                        exit_price, reason = row.Open, "타임스탑"
                    elif (pos.get("days_held", 0) >= 7 and not pos.get("trailing_active")
                          and row.Low <= pos["buy_price"]):
                        exit_price, reason = pos["buy_price"], "타임스탑"
                if exit_price is None:
                    bot.update_position_stop(pos, row.High)
            if exit_price is None:
                continue
            if pos["kind"] == "etf":
                proceeds = pos["shares"] * exit_price * (1 - ETF_ONE_WAY)
                gain = proceeds - pos["cost"]
                tax = DIVIDEND_TAX * max(0.0, gain) if code not in TAX_EXEMPT else 0.0
                proceeds -= tax
                traded_etf_today = True
            else:
                proceeds = pos["shares"] * exit_price * (1 - STOCK_SELL)
            cash += proceeds
            trades.append({
                "kind": pos["kind"], "code": code, "entry": pos["entry"], "exit": key,
                "strategy": pos["strategy"], "reason": reason,
                "ret": proceeds / pos["cost"] - 1, "pnl": proceeds - pos["cost"],
            })
            del positions[code]

        # ---- 계좌 낙폭 규칙 ----
        equity_open = equity_at(last_price)
        peak, _, size_mult, halted = bot.account_drawdown_controls(equity_open, peak)

        # ---- ETF 진입(시가) ----
        etf_pos = next((p for p in positions.values() if p["kind"] == "etf"), None)
        if (not halted and not etf_pos and target and not traded_etf_today
                and len(positions) < MAX_POSITIONS and target in etf_frames):
            row = row_of(etf_frames[target], d)
            prev = completed[target][-1] if completed[target] else None
            if row is not None and prev:
                ok, _ = bot.etf_entry_gap_status(prev["close"], row.Open, ETF_MAX_ADVERSE_ENTRY_GAP_PCT)
                if ok:
                    shares = bot.calculate_entry_shares(
                        equity_open, bot.entry_cash_after_reserve(equity_open, cash),
                        row.Open, ETF_ROTATION_STOP_LOSS_PCT, size_mult,
                        ETF_MAX_RISK_PER_TRADE_PCT,
                    )
                    shares = min(shares, int(equity_open * ETF_ROTATION_ALLOC_PCT // row.Open))
                    if shares >= 1:
                        cost = shares * row.Open * (1 + ETF_ONE_WAY)
                        cash -= cost
                        positions[target] = {
                            "kind": "etf", "code": target, "df": etf_frames[target],
                            "shares": shares, "cost": cost, "buy_price": row.Open,
                            "stop_loss_pct": ETF_ROTATION_STOP_LOSS_PCT,
                            "highest_price": row.Open, "days_held": 1,
                            "last_held_date": key, "entry": key,
                            "strategy": bot.ETF_ROTATION_STRATEGY,
                        }
                        bot.update_position_stop(positions[target], row.High)

        # ---- 개별주 진입(종가) ----
        held_inverse = any(c == ETF_ROTATION_INVERSE_CODE for c in positions)
        stock_blocked = target == ETF_ROTATION_INVERSE_CODE or held_inverse
        if (use_stocks and not halted and not stock_blocked and prev_d is not None
                and len(positions) < MAX_POSITIONS):
            pd_ = prev_d
            vol_prev = stocks["volume"].loc[pd_]
            pool = vol_prev.dropna().sort_values(ascending=False).index[:SCREENER_RANK_POOL]
            cap_prev = stocks["cap"].loc[pd_, pool]
            pool = cap_prev[(cap_prev >= MIN_MARKET_CAP_KRW) & (cap_prev <= MAX_MARKET_CAP_KRW)].index
            cands = []
            for code in pool:
                rsi = stocks["rsi"].at[pd_, code]
                cands.append((code, code, {
                    "previous_close": float(stocks["close"].at[pd_, code]),
                    "previous_trading_value": float(stocks["value"].at[pd_, code]),
                    "previous_rsi": None if pd.isna(rsi) else float(rsi),
                    "vbo_trend_ready": bool(stocks["ready"].at[pd_, code]),
                }))
            pre_account = {"ok": True, "total_assets": equity_open, "cash": cash}
            max_price = bot.screening_preferred_max_price(pre_account)
            universe = [c for _, c, _ in _mixed_universe_candidates(cands, SCREENER_TOP_N, max_price)]
            kospi_c = candles_upto(etf_data[MARKET_PROXY_CODE], d, 70)
            kosdaq_c = candles_upto(etf_data[KOSDAQ_MARKET_PROXY_CODE], d, 70)
            market = stocks["listing"]["market"]
            cd_kospi, cd_kosdaq = {}, {}
            for code in universe:
                if code in positions or d not in stocks["frames"][code].index:
                    continue
                c = candles_upto(stocks["frames"][code], d, 70)
                (cd_kosdaq if market.get(code) == "KOSDAQ" else cd_kospi)[code] = c
            signals = evaluate_signals(cd_kospi, kospi_c, 1.0) + evaluate_signals(cd_kosdaq, kosdaq_c, 1.0)
            signals.sort(key=lambda s: s["score"])
            for sig in signals:
                if len(positions) >= MAX_POSITIONS:
                    break
                code = sig["name"]
                price = sig["entry_price"]
                equity_now = equity_at(last_price)
                shares = bot.calculate_entry_shares(
                    equity_now, bot.entry_cash_after_reserve(equity_now, cash),
                    price, sig["stop_loss_pct"],
                    size_mult * float(sig.get("size_multiplier", 1.0)),
                )
                if shares < 1:
                    continue
                cost = shares * price * (1 + STOCK_BUY)
                if cost > cash:
                    continue
                cash -= cost
                atr = float(sig.get("atr", price * 0.03))
                positions[code] = {
                    "kind": "stock", "code": code, "df": stocks["frames"][code],
                    "shares": shares, "cost": cost, "buy_price": price,
                    "stop_loss_pct": sig["stop_loss_pct"], "highest_price": price,
                    "trailing_gap_pct": max(0.02, min(0.04, atr / max(price, 1))),
                    "days_held": 1, "last_held_date": key, "entry": key,
                    "strategy": sig["strategy"],
                }

        # ---- 대기자금(10% 버퍼 제외 현금, 세후 CD ETF 수익) ----
        equity = equity_at(last_price)
        if use_parking and park is not None and prev_d is not None and d in park.index:
            prev_p = park["Close"][park.index < d]
            if len(prev_p):
                r = park.at[d, "Close"] / prev_p.iloc[-1] - 1
                parked = max(0.0, cash - equity * (1 - MAX_TOTAL_EXPOSURE_PCT))
                cash += parked * r * ((1 - DIVIDEND_TAX) if r > 0 else 1)
                equity = equity_at(last_price)
        curve.append((d, equity, len(positions)))
        prev_d = d

    return pd.DataFrame(curve, columns=["date", "equity", "n"]).set_index("date"), trades


def summarize(name, curve, trades, capital, kind=None):
    eq = curve["equity"]
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    monthly = eq.resample("ME").last().pct_change().dropna()
    sel = [t for t in trades if kind is None or t["kind"] == kind]
    wins = [t for t in sel if t["ret"] > 0]
    gross_win = sum(t["pnl"] for t in sel if t["pnl"] > 0)
    gross_loss = -sum(t["pnl"] for t in sel if t["pnl"] < 0)
    if kind is not None:
        return {
            "name": name, "cagr_pct": None, "monthly_avg_pct": None, "mdd_pct": None,
            "trades": len(sel),
            "win_rate_pct": round(len(wins) / len(sel) * 100, 0) if sel else None,
            "avg_trade_pct": round(np.mean([t["ret"] for t in sel]) * 100, 2) if sel else None,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
            "net_pnl_pct_of_capital": round(sum(t["pnl"] for t in sel) / capital * 100, 1),
        }
    return {
        "name": name,
        "cagr_pct": round(((eq.iloc[-1] / capital) ** (1 / years) - 1) * 100, 2),
        "monthly_avg_pct": round(monthly.mean() * 100, 2),
        "mdd_pct": round((eq / eq.cummax() - 1).min() * 100, 1),
        "trades": len(sel),
        "win_rate_pct": round(len(wins) / len(sel) * 100, 0) if sel else None,
        "avg_trade_pct": round(np.mean([t["ret"] for t in sel]) * 100, 2) if sel else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2017-01-02")
    parser.add_argument("--capital", type=float, default=330000)
    args = parser.parse_args()
    etf_data = load_prices(list(ETF_ROTATION_ASSETS) + [PARKING_ETF_CODE], "2014-01-01", CACHE)
    dates = etf_data["069500"].index
    stocks = load_stocks(dates)
    print(f"종목 {len(stocks['frames'])}개 로드", flush=True)
    rows = []
    results = {}
    for label, use_stocks in (("ETF+개별주+대기자금", True), ("ETF+대기자금(개별주 끔)", False)):
        curve, trades = run(etf_data, stocks, args.start, args.capital, use_stocks, True)
        results[label] = (curve, trades)
        rows.append(summarize(label, curve, trades, args.capital))
    curve, trades = results["ETF+개별주+대기자금"]
    rows.append(summarize("  └ 개별주 매매만", curve, trades, args.capital, "stock"))
    print(f"기간 {curve.index[0].date()} ~ {curve.index[-1].date()}, 원금 {args.capital:,.0f}")
    print(pd.DataFrame(rows).to_string(index=False))
    stock_trades = pd.DataFrame([t for t in trades if t["kind"] == "stock"])
    if len(stock_trades):
        print("\n개별주 전략별")
        print(stock_trades.groupby("strategy")["ret"].agg(
            trades="count", avg_pct=lambda s: round(s.mean() * 100, 2),
            win_pct=lambda s: round((s > 0).mean() * 100, 0)).to_string())
        print("\n청산 사유")
        print(stock_trades.groupby("reason")["ret"].agg(
            trades="count", avg_pct=lambda s: round(s.mean() * 100, 2)).to_string())
        stock_trades["year"] = stock_trades["exit"].str[:4]
        print("\n연도별 개별주")
        print(stock_trades.groupby("year")["ret"].agg(
            trades="count", avg_pct=lambda s: round(s.mean() * 100, 2)).to_string())


if __name__ == "__main__":
    main()

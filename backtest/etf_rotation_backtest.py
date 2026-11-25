"""다중자산 ETF 추세전환 + 대기자금 백테스트.

실전 코드(bot.multi_asset_trend_selection, bot.update_position_stop)를 그대로 호출한다.
데이터: FinanceDataReader(네이버 금융) 일봉. 분배금 미반영(가격 기준).

가정
- 신호는 전일까지 확정된 일봉으로 계산, 체결은 당일 시가(09:15 진입 근사)
- 국면 전환 청산도 당일 시가, 청산 당일 재진입 금지(실전과 동일)
- 손절: 시가가 손절선 아래면 시가, 장중 저가가 닿으면 손절가에 체결
- 비용: 수수료 0.015% + 슬리피지 0.05% (편도), 국내 주식형(069500, 229200) 외 ETF는
  매매차익 15.4% 과세, 대기자금 수익도 15.4% 과세
"""
import argparse
import datetime
import math
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bot  # noqa: E402
from config import (  # noqa: E402
    ETF_ROTATION_ASSETS, ETF_ROTATION_ALLOC_PCT, ETF_ROTATION_STOP_LOSS_PCT,
    ETF_MAX_RISK_PER_TRADE_PCT, ETF_MAX_ADVERSE_ENTRY_GAP_PCT,
    BROKER_COMMISSION_PCT, ETF_SLIPPAGE_ESTIMATE_PCT, MAX_TOTAL_EXPOSURE_PCT,
    PARKING_ETF_CODE,
)

TAX_EXEMPT = {"069500", "229200"}
DIVIDEND_TAX = 0.154
ONE_WAY = BROKER_COMMISSION_PCT + ETF_SLIPPAGE_ESTIMATE_PCT


def load_prices(codes, start, cache_dir):
    import FinanceDataReader as fdr
    os.makedirs(cache_dir, exist_ok=True)
    data = {}
    for code in codes:
        path = os.path.join(cache_dir, f"{code}.csv")
        if os.path.exists(path):
            df = pd.read_csv(path, index_col=0, parse_dates=True)
        else:
            df = fdr.DataReader(code, start)
            df.to_csv(path)
        df = df[df["Close"] > 0]
        # 시가가 0으로 기록된 날은 종가로 대체
        df.loc[df["Open"] <= 0, "Open"] = df["Close"]
        data[code] = df
    return data


def old_update_position_stop(pos, current_price):
    """변경 전(2.5% 손절·+2/+3/+5% 단계 보호·+6%부터 1.5% 트레일링) ETF 청산 규칙."""
    buy = float(pos["buy_price"])
    stop_pct = float(pos.get("stop_loss_pct", 0.025))
    high = max(float(pos.get("highest_price", buy)), float(current_price))
    pos["highest_price"] = high
    stop = buy * (1 - stop_pct)
    locked = float(pos.get("trailing_stop_price", 0))
    if high >= buy * 1.06:
        stop = max(locked, high * (1 - 0.015))
    elif high >= buy * 1.05:
        stop = max(locked, buy * 1.03)
    elif high >= buy * 1.03:
        stop = max(locked, buy * 1.01)
    elif high >= buy * 1.02:
        stop = max(locked, buy)
    else:
        return stop, "초기 손절"
    pos["trailing_stop_price"] = stop
    return stop, "보호/추적"


def run(data, start, capital, policy="new", parking=True):
    if policy == "new":
        stop_fn, stop_pct, risk_pct = (
            bot.update_position_stop, ETF_ROTATION_STOP_LOSS_PCT,
            ETF_MAX_RISK_PER_TRADE_PCT,
        )
    else:
        stop_fn, stop_pct, risk_pct = old_update_position_stop, 0.025, 0.007

    dates = data["069500"].index
    dates = dates[dates >= pd.Timestamp(start)]
    hist = {
        code: [
            {"date": d.strftime("%Y%m%d"), "open": r.Open, "high": r.High,
             "low": r.Low, "close": r.Close}
            for d, r in df.iterrows()
        ]
        for code, df in data.items() if code in ETF_ROTATION_ASSETS
    }
    index_of = {code: {c["date"]: i for i, c in enumerate(rows)}
                for code, rows in hist.items()}
    park_df = data.get(PARKING_ETF_CODE)

    cash = float(capital)
    pos = None
    trades = []
    equity_curve = []
    prev_date = None
    for d in dates:
        key = d.strftime("%Y%m%d")
        now = datetime.datetime(d.year, d.month, d.day, 9, 15)
        completed = {}
        for code, rows in hist.items():
            # 전일까지 확정 일봉만 (오늘 이전 행)
            idx = index_of[code].get(key)
            if idx is None:
                # 오늘 거래가 없으면 오늘 이전 전체
                past = [r for r in rows if r["date"] < key][-130:]
            else:
                past = rows[max(0, idx - 130):idx]
            completed[code] = past
        regime, target, _ = bot.multi_asset_trend_selection(
            completed, pos["code"] if pos else None, now
        )
        traded_today = False

        def today_row(code):
            i = index_of[code].get(key)
            return hist[code][i] if i is not None else None

        if pos:
            row = today_row(pos["code"])
            if row:
                exit_price, reason = None, None
                if target != pos["code"]:
                    exit_price, reason = row["open"], "국면 전환"
                else:
                    stop, _ = stop_fn(pos, pos["highest_price"])
                    if row["open"] <= stop:
                        exit_price, reason = row["open"], "손절/보호(갭)"
                    elif row["low"] <= stop:
                        exit_price, reason = stop, "손절/보호"
                    else:
                        stop_fn(pos, row["high"])
                if exit_price:
                    proceeds = pos["shares"] * exit_price * (1 - ONE_WAY)
                    gain = proceeds - pos["cost"]
                    tax = (
                        DIVIDEND_TAX * max(0.0, gain)
                        if pos["code"] not in TAX_EXEMPT else 0.0
                    )
                    cash += proceeds - tax
                    trades.append({
                        "entry": pos["entry_date"], "exit": key,
                        "code": pos["code"], "reason": reason,
                        "ret": (proceeds - tax) / pos["cost"] - 1,
                    })
                    pos = None
                    traded_today = True

        if pos is None and target and not traded_today:
            row = today_row(target)
            prev = completed[target][-1] if completed[target] else None
            if row and prev:
                ok, _ = bot.etf_entry_gap_status(
                    prev["close"], row["open"], ETF_MAX_ADVERSE_ENTRY_GAP_PCT
                )
                if ok:
                    equity = cash
                    price = row["open"] * (1 + ONE_WAY)
                    available = max(0.0, cash - equity * (1 - MAX_TOTAL_EXPOSURE_PCT))
                    shares = min(
                        int(equity * ETF_ROTATION_ALLOC_PCT // price),
                        int(equity * risk_pct // (row["open"] * stop_pct)),
                        int(available // price),
                    )
                    if shares >= 1:
                        cost = shares * price
                        cash -= cost
                        pos = {
                            "code": target, "shares": shares, "cost": cost,
                            "buy_price": row["open"], "stop_loss_pct": stop_pct,
                            "highest_price": row["open"],
                            "strategy": bot.ETF_ROTATION_STRATEGY,
                            "entry_date": key,
                        }
                        stop_fn(pos, row["high"])

        # 대기자금: 10% 버퍼를 뺀 현금이 CD금리 ETF 일간 수익률을 받는다(세후).
        position_value = 0.0
        if pos:
            row = today_row(pos["code"])
            last = row["close"] if row else completed[pos["code"]][-1]["close"]
            position_value = pos["shares"] * last
        equity = cash + position_value
        if parking and park_df is not None and prev_date is not None:
            if key in park_df.index.strftime("%Y%m%d"):
                p_today = park_df.loc[d, "Close"] if d in park_df.index else None
                p_prev = park_df["Close"][park_df.index < d]
                if p_today is not None and len(p_prev):
                    r = p_today / p_prev.iloc[-1] - 1
                    parked = max(0.0, cash - equity * (1 - MAX_TOTAL_EXPOSURE_PCT))
                    cash += parked * r * (1 - DIVIDEND_TAX if r > 0 else 1)
                    equity = cash + position_value
        equity_curve.append((d, equity, pos["code"] if pos else None))
        prev_date = d

    curve = pd.DataFrame(equity_curve, columns=["date", "equity", "held"]).set_index("date")
    return curve, trades


def summarize(name, curve, trades, capital):
    eq = curve["equity"]
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / capital) ** (1 / years) - 1 if years > 0 else 0
    mdd = (eq / eq.cummax() - 1).min()
    monthly = eq.resample("ME").last().pct_change().dropna()
    wins = [t for t in trades if t["ret"] > 0]
    exposure = curve["held"].notna().mean()
    return {
        "name": name,
        "period": f"{eq.index[0].date()}~{eq.index[-1].date()}",
        "total_return_pct": round((eq.iloc[-1] / capital - 1) * 100, 1),
        "cagr_pct": round(cagr * 100, 2),
        "monthly_avg_pct": round(monthly.mean() * 100, 2),
        "monthly_std_pct": round(monthly.std() * 100, 2),
        "losing_months_pct": round((monthly < 0).mean() * 100, 0),
        "mdd_pct": round(mdd * 100, 1),
        "trades": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 0) if trades else None,
        "avg_trade_pct": round(sum(t["ret"] for t in trades) / len(trades) * 100, 2) if trades else None,
        "exposure_pct": round(exposure * 100, 0),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2016-06-01")
    parser.add_argument("--capital", type=float, default=330000)
    parser.add_argument("--cache", default=os.path.join(ROOT, "backtest", ".cache"))
    args = parser.parse_args()
    codes = list(ETF_ROTATION_ASSETS) + [PARKING_ETF_CODE]
    data = load_prices(codes, "2014-01-01", args.cache)
    rows = []
    for policy in ("new", "old"):
        for parking in (True, False):
            curve, trades = run(data, args.start, args.capital, policy, parking)
            rows.append(summarize(
                f"{policy}{' +대기자금' if parking else ''}", curve, trades, args.capital
            ))
    k = data["069500"]["Close"]
    k = k[k.index >= pd.Timestamp(args.start)]
    bh = pd.DataFrame({"equity": k / k.iloc[0] * args.capital, "held": "069500"})
    rows.append(summarize("KODEX200 보유", bh, [], args.capital))
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()

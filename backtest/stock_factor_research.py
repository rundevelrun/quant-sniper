"""개별주 대안 전략 탐색: 월 1회 리밸런싱 가격 기반 팩터 비교.

- 유니버스: 추정 시총 5천억~5조, 최근 20일 평균 거래대금 10억 이상(신호일 기준)
- 신호: 월말 종가 → 다음 거래일 종가에 매매 → 그 다음 날부터 수익 반영
- 비용: 매수 0.265%, 매도 0.465%(거래세 포함)를 회전율에 부과
- 동일가중 상위 N종목. 정수 주식 제약은 여기서 무시(전체 계좌 검증은 full_backtest)
- 한계: 현재 상장 종목만(생존편향), 분배금·배당 미반영
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backtest"))
from full_backtest import load_stocks, CACHE  # noqa: E402
from etf_rotation_backtest import load_prices  # noqa: E402

BUY_COST, SELL_COST = 0.00265, 0.00465


def factors(close, volume):
    ret = close.pct_change(fill_method=None)
    f = {}
    f["모멘텀12-1"] = close.shift(21) / close.shift(252) - 1
    f["모멘텀6-1"] = close.shift(21) / close.shift(126) - 1
    f["저변동성"] = -ret.rolling(120).std()
    f["52주고가근접"] = close / close.rolling(252).max()
    f["단기반전(1개월 낙폭)"] = -(close / close.shift(21) - 1)
    vol = ret.rolling(120).std()
    f["모멘텀/변동성"] = f["모멘텀12-1"] / (vol * np.sqrt(252))
    rank = lambda x: x.rank(axis=1, pct=True)
    f["모멘텀+저변동성"] = rank(f["모멘텀12-1"]) + rank(f["저변동성"])
    f["52주고가+저변동성"] = rank(f["52주고가근접"]) + rank(f["저변동성"])
    return f


def backtest(score, close, eligible, market_ok, top_n=10, trend=None):
    dates = close.index
    month_end = pd.Series(dates, index=dates).groupby([dates.year, dates.month]).last().values
    ret = close.pct_change(fill_method=None).fillna(0)
    weights = pd.DataFrame(0.0, index=dates, columns=close.columns)
    current = pd.Series(0.0, index=close.columns)
    signal_dates = set(pd.DatetimeIndex(month_end))
    pending = None
    for i, d in enumerate(dates):
        if pending is not None:
            current = pending
            pending = None
        weights.loc[d] = current
        if d in signal_dates:
            s = score.loc[d].where(eligible.loc[d])
            if trend is not None:
                s = s.where(trend.loc[d])
            s = s.dropna()
            new = pd.Series(0.0, index=close.columns)
            if market_ok is None or bool(market_ok.loc[d]):
                picks = s.sort_values(ascending=False).index[:top_n]
                if len(picks):
                    new[picks] = 1.0 / top_n
            pending = new  # 다음 거래일 종가에 반영
    # weights[d] = d일 종가 기준 보유 → d+1 수익
    held = weights.shift(1).fillna(0)
    gross = (held * ret).sum(axis=1)
    delta = weights.diff().fillna(weights)
    cost = delta.clip(lower=0).sum(axis=1) * BUY_COST + (-delta.clip(upper=0)).sum(axis=1) * SELL_COST
    net = gross - cost.shift(1).fillna(0)
    return net, delta.abs().sum(axis=1).sum() / 2 / (len(dates) / 252)


def stats(net, start, end):
    r = net[start:end]
    if len(r) < 50:
        return None
    eq = (1 + r).cumprod()
    years = len(r) / 252
    monthly = eq.resample("ME").last().pct_change().dropna()
    return {
        "cagr": round((eq.iloc[-1] ** (1 / years) - 1) * 100, 1),
        "mdd": round((eq / eq.cummax() - 1).min() * 100, 1),
        "sharpe": round(r.mean() / r.std() * np.sqrt(252), 2) if r.std() else 0,
        "month": round(monthly.mean() * 100, 2),
    }


def main():
    etf = load_prices(["069500"], "2014-01-01", CACHE)
    dates = etf["069500"].index
    st = load_stocks(dates)
    close, volume, cap = st["close"], st["volume"], st["cap"]
    value20 = (close * volume).rolling(20).mean()
    eligible = (cap >= 5e11) & (cap <= 5e12) & (value20 >= 1e9) & close.notna()
    k = etf["069500"]["Close"].reindex(dates)
    market_ok = k > k.rolling(120).mean()
    above_ma = close > close.rolling(120).mean()
    f = factors(close, volume)
    rows = []
    for name, score in f.items():
        for filt_name, mk, tr in (("필터 없음", None, None), ("시장+종목 추세필터", market_ok, above_ma)):
            for n in (5, 10):
                net, turnover = backtest(score, close, eligible, mk, n, tr)
                a = stats(net, "2017-01-01", "2021-12-31")
                b = stats(net, "2022-01-01", "2026-12-31")
                rows.append({
                    "factor": name, "filter": filt_name, "N": n,
                    "IS_cagr": a["cagr"], "IS_mdd": a["mdd"], "IS_sharpe": a["sharpe"],
                    "OOS_cagr": b["cagr"], "OOS_mdd": b["mdd"], "OOS_sharpe": b["sharpe"],
                    "turnover/yr": round(turnover, 1),
                })
    df = pd.DataFrame(rows)
    pd.set_option("display.width", 250)
    print(df.to_string(index=False))
    bh = k.pct_change().fillna(0)
    print("KODEX200", stats(bh, "2017-01-01", "2021-12-31"), stats(bh, "2022-01-01", "2026-12-31"))
    eqw = close.pct_change(fill_method=None).where(eligible.shift(1)).mean(axis=1).fillna(0)
    print("유니버스 동일가중", stats(eqw, "2017-01-01", "2021-12-31"), stats(eqw, "2022-01-01", "2026-12-31"))


if __name__ == "__main__":
    main()

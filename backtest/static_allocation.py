"""고정 비중 자산배분(월 1회 리밸런싱) 검증. 타이밍이 아니라 분산에서 나오는 수익을 본다.

비용: 리밸런싱 회전분에 편도 0.065%. 분배금·세금 미반영. 데이터는 캐시된 ETF 일봉.
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backtest"))
from etf_rotation_backtest import load_prices, ONE_WAY  # noqa: E402

CACHE = os.path.join(ROOT, "backtest", ".cache")
K, NQ, SP, B3, B10, GOLD, USD = "069500", "133690", "219480", "114260", "148070", "132030", "261240"
PORTFOLIOS = {
    "주식60/채권40": {K: .30, NQ: .30, B10: .40},
    "영구 포트폴리오(주식·채권·금·달러 25%씩)": {K: .125, NQ: .125, B10: .25, GOLD: .25, USD: .25},
    "올웨더형(주식30·채권40·금15·달러15)": {K: .15, NQ: .15, B10: .40, GOLD: .15, USD: .15},
    "균형형(주식50·채권25·금15·달러10)": {K: .20, NQ: .20, SP: .10, B10: .25, GOLD: .15, USD: .10},
    "안정형(주식20·단기채50·금15·달러15)": {K: .10, NQ: .10, B3: .50, GOLD: .15, USD: .15},
}


def simulate(close, weights, start):
    ret = close.pct_change().fillna(0)
    dates = close.index[close.index >= start]
    month = pd.Series(dates, index=dates).groupby([dates.year, dates.month]).first().values
    w = pd.Series(weights)
    hold = w.copy()
    equity, curve = 1.0, []
    for d in dates:
        r = ret.loc[d, w.index]
        hold = hold * (1 + r)
        equity *= hold.sum() / 1.0 if False else 1.0
        total = hold.sum()
        if d in month:
            cost = (hold / total - w).abs().sum() * ONE_WAY
            total *= (1 - cost)
            hold = w * total
        curve.append(total)
    return pd.Series(curve, index=dates)


def report(name, eq):
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    r = eq.pct_change().dropna()
    ye = eq.resample("YE").last()
    yr = ye.pct_change()
    yr.iloc[0] = ye.iloc[0] / eq.iloc[0] - 1
    half = lambda a, b: round(((eq[a:b].iloc[-1] / eq[a:b].iloc[0]) ** (365.25 / (eq[a:b].index[-1] - eq[a:b].index[0]).days) - 1) * 100, 1)
    print(f"{name}: 연 {((eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1) * 100:.1f}% / 낙폭 {(eq / eq.cummax() - 1).min() * 100:.1f}% / "
          f"샤프 {r.mean() / r.std() * np.sqrt(252):.2f} / 앞 {half('2017', '2021')}% 뒤 {half('2022', '2026')}% / "
          f"플러스 해 {(yr > 0).sum()}/{len(yr)} / 최악의 해 {yr.min() * 100:.1f}%")
    print("   연도별:", {d.year: round(v * 100, 1) for d, v in yr.items()})


def main():
    data = load_prices([K, NQ, SP, B3, B10, GOLD, USD], "2014-01-01", CACHE)
    close = pd.DataFrame({c: d["Close"] for c, d in data.items()}).ffill()
    start = pd.Timestamp("2017-01-02")
    for name, w in PORTFOLIOS.items():
        report(name, simulate(close, w, start))


if __name__ == "__main__":
    main()

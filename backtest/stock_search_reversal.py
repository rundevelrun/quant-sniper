"""개별주 단기 반전(눌림목 매수) 계열 탐색. 종가 매수 → k거래일 보유 → 종가 매도.

매일 신호 종목 중 낙폭이 큰 순으로 최대 M개를 사서 k일 보유하는 겹침 포트폴리오.
비용: 매수 0.265% + 매도 0.465%.
"""
import itertools
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backtest"))
from full_backtest import load_stocks, CACHE, sma_rsi  # noqa: E402
from etf_rotation_backtest import load_prices  # noqa: E402
from stock_search import stats, IS, OOS, BUY_COST, SELL_COST  # noqa: E402


def run(score, ret, gate, m, k):
    T, S = score.shape
    entries = np.zeros((T, S))
    for i in range(T):
        if not gate[i]:
            continue
        row = score[i]
        valid = np.flatnonzero(~np.isnan(row))
        if len(valid):
            top = valid[np.argsort(-row[valid])[:m]]
            entries[i, top] = 1.0 / (m * k)
    W = np.zeros((T, S))
    for j in range(k):                       # 진입일 종가 매수 → 이후 k일 수익
        W[j + 1:] += entries[:T - j - 1]
    gross = (W * ret).sum(axis=1)
    traded = entries.sum(axis=1)
    cost = traded * BUY_COST
    cost[k:] += traded[:-k] * SELL_COST
    return gross - cost


def main():
    etf = load_prices(["069500"], "2014-01-01", CACHE)
    dates = etf["069500"].index
    st = load_stocks(dates)
    close, volume, cap = st["close"], st["volume"], st["cap"]
    ret = close.pct_change(fill_method=None).fillna(0).values
    liquid = ((close * volume).rolling(20).mean() >= 1e9) & close.notna()
    k200 = etf["069500"]["Close"].reindex(dates)
    rsi14, rsi3 = st["rsi"], close.apply(lambda s: sma_rsi(s, 3))
    r5 = close / close.shift(5) - 1
    down3 = (close.diff() < 0).rolling(3).sum() == 3
    triggers = {
        "RSI14<30": (rsi14 < 30, -rsi14),
        "RSI3<10": (rsi3 < 10, -rsi3),
        "5일 -10% 이하": (r5 <= -0.10, -r5),
        "3일 연속 하락": (down3, -(close / close.shift(3) - 1)),
    }
    trends = {"없음": True, "종목>120일선": close > close.rolling(120).mean()}
    universes = {
        "5천억~5조": (cap >= 5e11) & (cap <= 5e12),
        "1조~20조": (cap >= 1e12) & (cap <= 2e13),
    }
    gates = {
        "없음": np.ones(len(dates), bool),
        "K200>120일선": (k200 > k200.rolling(120).mean()).values,
    }
    is_mask = (dates >= IS[0]) & (dates <= IS[1])
    oos_mask = (dates >= OOS[0]) & (dates <= OOS[1])
    rows = []
    for (tn, (cond, sc)), (rn, tr), (un, u), (gn, g), k in itertools.product(
            triggers.items(), trends.items(), universes.items(), gates.items(), (3, 5, 10)):
        elig = cond & u & liquid
        if tr is not True:
            elig &= tr
        net = run(sc.where(elig).values, ret, g, 2, k)
        a, b = stats(net, is_mask), stats(net, oos_mask)
        rows.append({"trigger": tn, "trend": rn, "universe": un, "gate": gn, "hold": k,
                     "IS_cagr": a[0], "IS_mdd": a[1], "IS_sharpe": a[2],
                     "OOS_cagr": b[0], "OOS_mdd": b[1], "OOS_sharpe": b[2]})
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(CACHE, "stock_search_reversal.csv"), index=False)
    pd.set_option("display.width", 250)
    print(out.sort_values("IS_sharpe", ascending=False).head(15).to_string(index=False))
    print("IS-OOS sharpe corr", round(out.IS_sharpe.corr(out.OOS_sharpe), 2))
    ok = out[(out.IS_cagr >= 10) & (out.OOS_cagr >= 10) & (out.IS_mdd >= -30) & (out.OOS_mdd >= -30)]
    print("두 기간 통과", len(ok), "/", len(out))
    print(ok.to_string(index=False))
    print(out.groupby("trigger")[["IS_cagr", "OOS_cagr", "IS_mdd", "OOS_mdd"]].median().round(1))


if __name__ == "__main__":
    main()

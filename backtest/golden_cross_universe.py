"""전 종목 골든크로스 감시 매매(연구용).

매일 종가 기준으로 50일선이 200일선을 상향 돌파한 종목을 다음 날 시가에 매수,
데드크로스(50일선 < 200일선) 또는 손절 시 다음 날 시가에 매도.
동시 보유 최대 N종목(자금 N등분), 신호가 많으면 거래대금 큰 순.
유동성: 20일 평균 거래대금 10억 원 이상, 주가 1,000원 이상.
비용: 매수 0.265%, 매도 0.465%(거래세 포함). 생존 편향 있음(현재 상장 종목만).
비교: KODEX 200 보유. 구간 2016–2020 / 2021–2026.
"""
import itertools
import os
from multiprocessing import Pool

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "backtest", ".cache")
BUY, SELL = 0.00265, 0.00465
_G = {}


def init():
    frames = {}
    for f in os.listdir(os.path.join(CACHE, "stocks")):
        if f.endswith(".csv"):
            frames[f[:-4]] = pd.read_csv(os.path.join(CACHE, "stocks", f), parse_dates=["Date"], index_col="Date")
    c = pd.DataFrame({k: v["Close"] for k, v in frames.items()}).sort_index()
    o = pd.DataFrame({k: v["Open"] for k, v in frames.items()}).reindex(c.index)
    v = pd.DataFrame({k: v["Volume"] for k, v in frames.items()}).reindex(c.index)
    c = c[c.index >= "2015-01-01"]
    o, v = o.reindex(c.index), v.reindex(c.index)
    m50, m200 = c.rolling(50).mean(), c.rolling(200).mean()
    above = m50 > m200
    _G.update(c=c, o=o.where(o > 0), value=(c * v).rolling(20).mean(),
              cross_up=above & ~above.shift(1, fill_value=False) & m200.notna(), above=above)


def run(args):
    n_max, stop, mkt = args
    c, o, value, up, above = (_G[k] for k in ("c", "o", "value", "cross_up", "above"))
    k200 = pd.read_csv(os.path.join(CACHE, "069500.csv"), index_col=0, parse_dates=True)["Close"].reindex(c.index).ffill()
    mkt_ok = k200 > k200.rolling(200).mean()
    dates = c.index
    cash, held = 1.0, {}  # code -> (shares, entry_price)
    curve, trades = [], []
    pending_buy, pending_sell = [], []
    for i, d in enumerate(dates):
        # 전일 신호 체결(시가)
        for code in pending_sell:
            if code in held:
                sh, ep = held.pop(code)
                px = o.at[d, code] if not np.isnan(o.at[d, code]) else c.at[d, code]
                cash += sh * px * (1 - SELL)
                trades.append(px / ep - 1)
        slots = n_max - len(held)
        for code in pending_buy[:max(0, slots)]:
            px = o.at[d, code]
            if np.isnan(px) or px <= 0:
                continue
            budget = (cash + sum(sh * c.at[dates[i - 1], cd] for cd, (sh, _) in held.items())) / n_max
            budget = min(budget, cash)
            if budget <= 0:
                continue
            held[code] = (budget * (1 - BUY) / px, px)
            cash -= budget
        pending_buy, pending_sell = [], []
        # 평가
        val = cash + sum(sh * (c.at[d, cd] if not np.isnan(c.at[d, cd]) else ep) for cd, (sh, ep) in held.items())
        curve.append(val)
        # 오늘 종가 신호 → 내일 체결
        for cd, (sh, ep) in held.items():
            px = c.at[d, cd]
            if (not above.at[d, cd]) or (stop and px <= ep * (1 - stop)):
                pending_sell.append(cd)
        if not mkt or mkt_ok.iloc[i]:
            cand = up.loc[d]
            cand = cand[cand].index
            cand = [x for x in cand if x not in held and value.at[d, x] >= 1e9 and c.at[d, x] >= 1000]
            pending_buy = sorted(cand, key=lambda x: -value.at[d, x])
    eq = pd.Series(curve, index=dates)
    r = eq.pct_change().fillna(0)
    def st(x):
        e = (1 + x).cumprod()
        return (e.iloc[-1] ** (252 / len(x)) - 1) * 100, (e / e.cummax() - 1).min() * 100
    f, a, b = st(r[r.index >= "2016-01-01"]), st(r["2016":"2020"]), st(r["2021":])
    tr = np.array(trades)
    return dict(최대보유=n_max, 손절=stop, 시장필터=mkt, 연=round(f[0], 1), MDD=round(f[1], 1),
                앞=round(a[0], 1), 뒤=round(b[0], 1), 매매=len(tr),
                승률=round((tr > 0).mean() * 100) if len(tr) else 0, 평균손익=round(tr.mean() * 100, 2) if len(tr) else 0)


def main():
    init()
    jobs = list(itertools.product([5, 10, 20], [None, 0.08, 0.15], [False, True]))
    with Pool(6, initializer=init) as pool:
        df = pd.DataFrame(pool.map(run, jobs))
    k = pd.read_csv(os.path.join(CACHE, "069500.csv"), index_col=0, parse_dates=True)["Close"]
    kr = k.pct_change().dropna()
    def st(x):
        e = (1 + x).cumprod()
        return (e.iloc[-1] ** (252 / len(x)) - 1) * 100, (e / e.cummax() - 1).min() * 100
    print("KODEX 200 보유: 연 %.1f%% MDD %.1f%% | 앞 %.1f%% 뒤 %.1f%%" % (*st(kr["2016":]), st(kr["2016":"2020"])[0], st(kr["2021":])[0]))
    pd.set_option("display.width", 200)
    print(df.sort_values("연", ascending=False).to_string(index=False))


if __name__ == "__main__":
    main()

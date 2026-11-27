"""레버리지 ETF 단일 상품 추세 진입·청산 전수 탐색(연구용, 운용 정책과 별개).

상품: 미국 2·3배(SOXL, TQQQ, UPRO, TECL, FAS, TNA, LABU, TMF, UGL, NUGT, QLD, SSO, USD, ROM)
      국내 2배(KODEX 레버리지 122630, KODEX 코스닥150레버리지 233740)
신호는 해당 상품 자체 종가 기준(전일 신호 → 다음 날 보유).
비용: 미국 편도 0.35%(수수료·환전·스프레드 추정), 국내 편도 0.065%. 세금·환율 미반영.
판정: ~2018, 2019~ 두 구간 모두 연수익 > 0.
"""
import itertools
import json
import os
from multiprocessing import Pool

import numpy as np
import pandas as pd

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", "yahoo")
US = ["SOXL", "TQQQ", "UPRO", "TECL", "FAS", "TNA", "LABU", "TMF", "UGL", "NUGT", "QLD", "SSO", "USD", "ROM"]
KR = ["122630.KS", "233740.KS"]
NAMES = {"122630.KS": "KODEX 레버리지", "233740.KS": "KODEX 코스닥150레버리지"}
_G = {}


def ser(sym):
    f = "SOXL.json" if sym == "SOXL" else f"lev_{sym}.json"
    j = json.load(open(os.path.join(D, f)))["chart"]["result"][0]
    s = pd.Series(j["indicators"]["adjclose"][0]["adjclose"],
                  index=pd.to_datetime(j["timestamp"], unit="s").normalize())
    s = s[~s.index.duplicated()].dropna()
    return s[s > 0]


def stats(r):
    if len(r) < 120 or r.std() == 0:
        return np.nan, np.nan, np.nan
    e = (1 + r).cumprod()
    return ((e.iloc[-1] ** (252 / len(r)) - 1) * 100, (e / e.cummax() - 1).min() * 100,
            r.mean() / r.std() * np.sqrt(252))


def sig(x, kind, p):
    if kind == "MA":
        ma, band, slope = p
        m = x.rolling(ma).mean()
        s = x > m * (1 + band)
        return s & (m > m.shift(5)) if slope else s
    if kind == "CROSS":
        f, sl = p
        return x.rolling(f).mean() > x.rolling(sl).mean()
    if kind == "MOM":
        return x / x.shift(p[0]) - 1 > 0
    if kind == "VOLMA":
        ma, vcap = p
        vol = x.pct_change().rolling(20).std() * np.sqrt(252)
        return (x > x.rolling(ma).mean()) & (vol < vcap)


def run(job):
    sym, kind, p = job
    x = _G[sym]
    cost = 0.00065 if sym in KR else 0.0035
    pos = sig(x, kind, p).shift(1, fill_value=False).astype(float)
    r = x.pct_change().fillna(0) * pos - pos.diff().abs().fillna(0) * cost
    full, a, b = stats(r), stats(r[:"2018"]), stats(r["2019":])
    return dict(상품=NAMES.get(sym, sym), 신호=kind, 설정=str(p), 연=round(full[0], 1), MDD=round(full[1], 1),
                샤프=round(full[2], 2), 앞=round(a[0], 1), 뒤=round(b[0], 1),
                교체=int((pos.diff().abs() > 0).sum()), 통과=bool(a[0] > 0 and b[0] > 0))


def init():
    for s in US + KR:
        _G[s] = ser(s)


def jobs():
    out = []
    for sym in US + KR:
        for p in itertools.product([20, 50, 100, 150, 200, 250], [0, .01, .02, .03, .05], [False, True]):
            out.append((sym, "MA", p))
        for p in [(5, 20), (10, 50), (20, 50), (20, 100), (50, 150), (50, 200), (100, 250)]:
            out.append((sym, "CROSS", p))
        for n in (10, 20, 40, 60, 120, 200):
            out.append((sym, "MOM", (n,)))
        for p in itertools.product([50, 100, 200], [0.3, 0.4, 0.5, 0.7]):
            out.append((sym, "VOLMA", p))
    return out


def main():
    init()
    rows = []
    for sym in US + KR:
        r = _G[sym].pct_change().fillna(0)
        f = stats(r)
        rows.append((NAMES.get(sym, sym), _G[sym].index[0].date(), round(f[0], 1), round(f[1], 1), round(f[2], 2)))
    with Pool(6, initializer=init) as pool:
        df = pd.DataFrame(pool.map(run, jobs()))
    df.to_csv(os.path.join(os.path.dirname(D), "leverage_search.csv"), index=False)
    pd.set_option("display.width", 230)
    hold = pd.DataFrame(rows, columns=["상품", "시작", "보유연", "보유MDD", "보유샤프"]).set_index("상품")
    best = (df[df["통과"]].sort_values("샤프", ascending=False).groupby("상품").head(1)
            .set_index("상품")[["신호", "설정", "연", "MDD", "샤프", "앞", "뒤", "교체"]])
    share = (df.groupby("상품")["통과"].mean() * 100).round(0).rename("통과%")
    out = hold.join(share).join(best).sort_values("샤프", ascending=False)
    print(f"조합 {len(df)}개. 상품별: 그냥 보유 vs 두 기간 통과 조합 중 샤프 최고")
    print(out.to_string())


if __name__ == "__main__":
    main()

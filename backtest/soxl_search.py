"""SOXL 또는 SOXS 단일 상품 추세 진입·청산 전수 탐색(연구용, 운용 정책과 별개).

신호(전일 종가 기준 → 다음 날 보유)
  MA      : 기준 지수(SOX 또는 해당 ETF) 종가 > N일선×(1+밴드) [& N일선 상승]
  CROSS   : 단기선 > 장기선
  MOM     : N일 수익률 > 0
  VOLMA   : MA 조건 & 20일 변동성 < 기준 (변동성 급등 시 회피)
SOXS는 신호를 반대로(하락 추세일 때만 보유).
비용: 편도 0.35%(수수료·환전·스프레드 추정). 세금·환율 미반영(USD 기준).
판정: 2011–2018, 2019–2026 두 기간 모두 연수익 > 0, 전체 연수익 순 정렬.
"""
import itertools
import json
import os
from multiprocessing import Pool

import numpy as np
import pandas as pd

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", "yahoo")
COST = 0.0035
_G = {}


def ser(f):
    j = json.load(open(os.path.join(D, f)))["chart"]["result"][0]
    s = pd.Series(j["indicators"]["adjclose"][0]["adjclose"],
                  index=pd.to_datetime(j["timestamp"], unit="s").normalize())
    return s[~s.index.duplicated()].dropna()


def stats(r):
    e = (1 + r).cumprod()
    y = len(r) / 252
    return ((e.iloc[-1] ** (1 / y) - 1) * 100, (e / e.cummax() - 1).min() * 100,
            r.mean() / r.std() * np.sqrt(252) if r.std() else 0.0)


def signal(kind, src, p):
    x = _G[src]
    if kind == "MA":
        ma, band, slope = p
        m = x.rolling(ma).mean()
        s = x > m * (1 + band)
        return s & (m > m.shift(5)) if slope else s
    if kind == "CROSS":
        f, sl = p
        return x.rolling(f).mean() > x.rolling(sl).mean()
    if kind == "MOM":
        (n,) = p
        return x / x.shift(n) - 1 > 0
    if kind == "VOLMA":
        ma, vcap = p
        vol = x.pct_change().rolling(20).std() * np.sqrt(252)
        return (x > x.rolling(ma).mean()) & (vol < vcap)


def run(job):
    inst, kind, src, p = job
    up = signal(kind, src, p).reindex(_G["idx"]).fillna(False)
    want = up if inst == "SOXL" else ~up & _G[src].rolling(50).mean().notna()
    pos = want.shift(1, fill_value=False).astype(float)
    r = _G["r_" + inst] * pos - pos.diff().abs().fillna(0) * COST
    full, a, b = stats(r), stats(r[:"2018"]), stats(r["2019":])
    return dict(상품=inst, 신호=kind, 기준=src, 설정=str(p), 연=round(full[0], 1), MDD=round(full[1], 1),
                샤프=round(full[2], 2), 앞=round(a[0], 1), 뒤=round(b[0], 1),
                교체=int((pos.diff().abs() > 0).sum()), 통과=a[0] > 0 and b[0] > 0)


def init():
    L, S, X = ser("SOXL.json"), ser("SOXS.json"), ser("_SOX.json")
    idx = L.index.intersection(S.index).intersection(X.index)
    _G.update(idx=idx, SOX=X[idx], SOXL=L[idx], SOXS=S[idx],
              r_SOXL=L[idx].pct_change().fillna(0), r_SOXS=S[idx].pct_change().fillna(0))


def jobs():
    out = []
    for inst, src in itertools.product(["SOXL", "SOXS"], ["SOX", "SOXL"]):
        for ma, band, slope in itertools.product([20, 50, 100, 150, 200, 250], [0, .01, .02, .03, .05], [False, True]):
            out.append((inst, "MA", src, (ma, band, slope)))
        for f, sl in [(5, 20), (10, 50), (20, 50), (20, 100), (50, 150), (50, 200), (100, 250)]:
            out.append((inst, "CROSS", src, (f, sl)))
        for n in (10, 20, 40, 60, 120, 200):
            out.append((inst, "MOM", src, (n,)))
        for ma, v in itertools.product([50, 100, 200], [0.3, 0.4, 0.5, 0.7]):
            out.append((inst, "VOLMA", src, (ma, v)))
    return out


def main():
    init()
    base = stats(_G["r_SOXL"])
    with Pool(6, initializer=init) as pool:
        df = pd.DataFrame(pool.map(run, jobs()))
    df.to_csv(os.path.join(os.path.dirname(D), "soxl_search.csv"), index=False)
    pd.set_option("display.width", 220)
    print(f"조합 {len(df)}개 · SOXL 단순 보유 연 {base[0]:.1f}% MDD {base[1]:.1f}% 샤프 {base[2]:.2f}")
    print("상품별 통과 비율:", (df.groupby("상품")["통과"].mean() * 100).round(0).to_dict())
    for inst in ("SOXL", "SOXS"):
        sub = df[(df["상품"] == inst) & df["통과"]].sort_values("연", ascending=False)
        print(f"\n[{inst}] 두 기간 통과 상위 10 (전체 {len(sub)}개)")
        print(sub.head(10).drop(columns=["상품", "통과"]).to_string(index=False))
    print("\n신호 계열별 SOXL 연수익 중앙값:",
          df[df["상품"] == "SOXL"].groupby("신호")["연"].median().to_dict())


if __name__ == "__main__":
    main()

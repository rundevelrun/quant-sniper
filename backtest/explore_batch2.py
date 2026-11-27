"""탐색 2차: 아직 검증하지 않은 다섯 방향(연구용).

F1 국내 상장 ETF 전체(417개) 모멘텀 상위 k개 월간 교체 — 추세전환(30%) 대체 후보로 비교
F2 코어 위험 균형(변동성 역비례) vs 고정 비중
F3 계절성: 11–4월 주식(KODEX 200) / 5–10월 채권(국고채3년)
F4 배당 시즌: 고배당 ETF를 연말 배당 전 구간만 보유
F5 추세전환 앙상블(여러 기간 평균) — 기존 20개 설정 평균 결과 재확인
비용: ETF 편도 0.065%, 국내 주식형 외 ETF 매매차익 15.4%(이름으로 분류, 보수적).
판정: 2018–2021 / 2022–2026 두 기간 모두 비교 대상보다 나을 것.
"""
import itertools
import os
import re
import sys
from multiprocessing import Pool

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backtest"))
CACHE = os.path.join(ROOT, "backtest", ".cache")
U = os.path.join(CACHE, "etf_universe")
ONE_WAY = 0.00065
TAXED = re.compile("미국|차이나|중국|글로벌|골드|금|원유|달러|채권|국채|국고|일본|인도|베트남|유럽|나스닥|S&P|선진|신흥|리츠|은|구리|농산물|원자재|국공채|회사채|하이일드")
_G = {}


def st(r):
    r = r.dropna()
    if len(r) < 200 or r.std() == 0:
        return np.nan, np.nan, np.nan
    e = (1 + r).cumprod()
    return (e.iloc[-1] ** (252 / len(r)) - 1) * 100, (e / e.cummax() - 1).min() * 100, r.mean() / r.std() * np.sqrt(252)


def halves(r):
    return st(r["2018":"2021"]), st(r["2022":"2026"]), st(r["2018":])


def init():
    names = dict(l.rstrip("\n").split("\t") for l in open(os.path.join(U, "names.tsv"), encoding="utf-8"))
    close, value = {}, {}
    for code in names:
        p = os.path.join(U, f"{code}.csv")
        if os.path.exists(p):
            df = pd.read_csv(p, index_col=0, parse_dates=True)
            close[code], value[code] = df["Close"], df["Close"] * df["Volume"]
    c = pd.DataFrame(close).sort_index()
    c = c[c.index >= "2014-09-01"]
    _G.update(names=names, c=c, v=pd.DataFrame(value).reindex(c.index).rolling(20).mean(),
              taxed={k: bool(TAXED.search(n)) for k, n in names.items()})


def f1(args):
    top_k, lookback, trend = args
    c, v = _G["c"], _G["v"]
    ret = c.pct_change(fill_method=None)
    look = {"3m": [63], "6m": [126], "12m": [252], "3+6m": [63, 126], "1+3+6m": [21, 63, 126]}[lookback]
    score = sum(c / c.shift(n) - 1 for n in look) / len(look)
    ma = c.rolling(120).mean()
    dates = c.index
    month_end = set(pd.DatetimeIndex(pd.Series(dates, index=dates).groupby([dates.year, dates.month]).last().values))
    w = pd.Series(0.0, index=c.columns)
    out, pending = [], None
    for d in dates:
        cost = 0.0
        if pending is not None:
            diff = pending - w
            sells = (-diff.clip(upper=0))
            cost = diff.abs().sum() * ONE_WAY
            w, pending = pending, None
        out.append((w * ret.loc[d].fillna(0)).sum() - cost)
        if d in month_end:
            s = score.loc[d]
            ok = s.notna() & (v.loc[d] >= 1e9) & c.loc[d].notna()
            if trend:
                ok &= (c.loc[d] > ma.loc[d]) & (s > 0)
            s = s[ok].sort_values(ascending=False)
            new = pd.Series(0.0, index=c.columns)
            if len(s):
                picks = s.index[:top_k]
                new[picks] = 1.0 / top_k  # 후보가 부족하면 나머지는 현금
            pending = new
    r = pd.Series(out, index=dates)
    # 비국내주식 ETF 차익 과세를 연 단위로 근사: 양의 수익분의 15.4% × 과세 비중 평균
    return ("F1", f"상위{top_k} {lookback} 추세필터{trend}"), r


def core_series():
    def path(k):
        p = os.path.join(CACHE, f"{k}.csv")
        return p if os.path.exists(p) else os.path.join(U, f"{k}.csv")
    c = pd.DataFrame({k: pd.read_csv(path(k), index_col=0, parse_dates=True)["Close"]
                      for k in ("069500", "133690", "148070", "132030", "261240", "114260", "161510")}).ffill()
    return c


def f2():
    c = core_series()[["069500", "133690", "148070", "132030", "261240"]]
    ret = c.pct_change().fillna(0)
    fixed = pd.Series({"069500": .15, "133690": .15, "148070": .40, "132030": .15, "261240": .15})
    vol = ret.rolling(60).std()
    dates = ret.index
    month_end = set(pd.DatetimeIndex(pd.Series(dates, index=dates).groupby([dates.year, dates.month]).last().values))
    res = {}
    for name in ("고정 비중", "위험 균형(60일)"):
        w, out = fixed.copy(), []
        for d in dates:
            out.append((w * ret.loc[d]).sum())
            w = w * (1 + ret.loc[d])
            w = w / w.sum()
            if d in month_end:
                if name == "고정 비중":
                    new = fixed
                else:
                    iv = 1 / vol.loc[d]
                    new = iv / iv.sum() if iv.notna().all() else fixed
                out[-1] -= (new - w).abs().sum() * ONE_WAY
                w = new.copy()
        res[name] = pd.Series(out, index=dates)
    return res


def f3():
    c = core_series()
    ret = c.pct_change().fillna(0)
    m = ret.index.month
    winter = pd.Series((m >= 11) | (m <= 4), index=ret.index).shift(1, fill_value=False)
    switch = winter != winter.shift(1, fill_value=False)
    seasonal = ret["069500"].where(winter, ret["114260"]) - switch * 2 * ONE_WAY
    return {"KODEX 200 보유": ret["069500"], "11–4월 주식 / 5–10월 채권": seasonal}


def f4():
    c = core_series()
    ret = c.pct_change().fillna(0)
    dates = ret.index
    out = {"PLUS 고배당주 보유": ret["161510"]}
    for start_month in (10, 11):
        hold = pd.Series((dates.month >= start_month), index=dates).shift(1, fill_value=False)
        sw = hold != hold.shift(1, fill_value=False)
        out[f"{start_month}–12월만 보유(나머지 채권)"] = ret["161510"].where(hold, ret["114260"]) - sw * 2 * ONE_WAY
    return out


def main():
    init()
    jobs = list(itertools.product([3, 5], ["3m", "6m", "12m", "3+6m", "1+3+6m"], [False, True]))
    with Pool(6, initializer=init) as pool:
        f1_res = pool.map(f1, jobs)
    import etf_robustness as er
    dates, arr, si = er.prepare()
    eq, _ = er.simulate(dates, arr, si, alloc=1.0, **er.BASE)
    rot = eq.pct_change().fillna(0)
    pd.set_option("display.width", 220)
    print("[F1] 국내 ETF 417개 모멘텀 상위 k (추세전환 단독과 비교)")
    a, b, f = halves(rot)
    print(f"  기준: 현재 추세전환 단독 | 18-21 {a[0]:.1f}% 22-26 {b[0]:.1f}% | 전체 연 {f[0]:.1f}% MDD {f[1]:.1f}% 샤프 {f[2]:.2f}")
    rows = []
    for (fam, name), r in f1_res:
        a2, b2, f2_ = halves(r)
        rows.append((name, round(a2[0], 1), round(b2[0], 1), round(f2_[0], 1), round(f2_[1], 1), round(f2_[2], 2),
                     a2[0] > a[0] and b2[0] > b[0]))
    print(pd.DataFrame(rows, columns=["설정", "18-21", "22-26", "연", "MDD", "샤프", "두 기간 우위"]).sort_values("샤프", ascending=False).to_string(index=False))
    for title, res in (("[F2] 코어 위험 균형", f2()), ("[F3] 계절성", f3()), ("[F4] 배당 시즌", f4())):
        print(f"\n{title}")
        for name, r in res.items():
            a2, b2, f2_ = halves(r)
            print(f"  {name:28s} 18-21 {a2[0]:5.1f}% 22-26 {b2[0]:5.1f}% | 연 {f2_[0]:5.1f}% MDD {f2_[1]:6.1f}% 샤프 {f2_[2]:.2f}")
    print("\n[F5] 추세전환 앙상블: 20개 설정 평균(앙상블) 연 9.5% vs 현재 단일 설정 11.0% (combo 백테스트) — 앙상블은 수익을 낮추고 안정성만 높임(기존 결과)")


if __name__ == "__main__":
    main()

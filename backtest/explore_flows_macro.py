"""수급·거시 가설 검증(연구용).

M1 VIX 공포 반등: VIX가 기준 이상에서 꺾이면 KODEX 200 보유 H일
M2 장단기 금리차: 미국 10년-2년 역전(<0) 구간엔 채권, 아니면 주식
P1 프로그램 순매수: 코스피 비차익 순매수 N일 합이 상위 구간이면 다음 날 KODEX 200 보유
F1 외국인 순매수(데이터 수집 완료 시): N일 누적 순매수 > 0 이면 보유
모든 신호는 전일 확정값 → 다음 날 보유. 비용 편도 0.065%.
비교: KODEX 200 보유, 현재 추세전환(추세 필터) — 두 기간(2015–2020 / 2021–2026).
"""
import itertools
import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
C = os.path.join(ROOT, "backtest", ".cache")
ONE_WAY = 0.00065


def st(r):
    r = r.dropna()
    if len(r) < 200 or r.std() == 0:
        return (np.nan,) * 3
    e = (1 + r).cumprod()
    return (e.iloc[-1] ** (252 / len(r)) - 1) * 100, (e / e.cummax() - 1).min() * 100, r.mean() / r.std() * np.sqrt(252)


def row(name, r, base):
    a, b, f = st(r["2015":"2020"]), st(r["2021":]), st(r["2015":])
    ba, bb = st(base["2015":"2020"]), st(base["2021":])
    win = a[2] > ba[2] and b[2] > bb[2]
    return dict(가설=name, 앞=round(a[0], 1), 뒤=round(b[0], 1), 연=round(f[0], 1), MDD=round(f[1], 1),
                샤프=round(f[2], 2), 보유시간=round(float(r.ne(0).mean()) * 100), 두기간샤프우위=win)


def strat(ret, hold, alt=None):
    hold = hold.reindex(ret.index).fillna(False).astype(bool).shift(1, fill_value=False)
    other = alt.reindex(ret.index).fillna(0) if alt is not None else 0.0
    r = ret.where(hold, other)
    sw = hold != hold.shift(1, fill_value=False)
    return r - sw * 2 * ONE_WAY


def main():
    k = pd.read_csv(os.path.join(C, "069500.csv"), index_col=0, parse_dates=True)["Close"]
    bond = pd.read_csv(os.path.join(C, "114260.csv"), index_col=0, parse_dates=True)["Close"].reindex(k.index).ffill()
    ret, bret = k.pct_change().fillna(0), bond.pct_change().fillna(0)
    base = ret
    rows = [row("KODEX 200 보유", ret, base)]
    ma = k.rolling(60).mean()
    rows.append(row("60일선 위 보유(참고)", strat(ret, k > ma * 1.01), base))

    j = json.load(open(os.path.join(C, "macro", "VIX.json")))["chart"]["result"][0]
    vix = pd.Series(j["indicators"]["quote"][0]["close"], index=pd.to_datetime(j["timestamp"], unit="s").normalize())
    vix = vix[~vix.index.duplicated()].dropna().reindex(k.index, method="ffill")
    for lvl, hold_days in itertools.product([20, 25, 30, 35], [5, 10, 20, 40]):
        trig = (vix.shift(1) >= lvl) & (vix < vix.shift(1) * 0.9)
        hold = trig.astype(int).rolling(hold_days, min_periods=1).max().astype(bool)
        rows.append(row(f"M1 VIX≥{lvl} 꺾임 후 {hold_days}일 보유", strat(ret, hold), base))

    curve = pd.read_csv(os.path.join(C, "macro", "T10Y2Y.csv"), index_col=0, parse_dates=True).iloc[:, 0]
    curve = pd.to_numeric(curve, errors="coerce").dropna().reindex(k.index, method="ffill")
    for th in (0.0, 0.25, 0.5):
        rows.append(row(f"M2 금리차>{th} 주식/아니면 채권", strat(ret, curve > th, bret), base))

    p = pd.read_csv(os.path.join(C, "flows", "program_KOSPI.csv"), dtype={"date": str})
    p.index = pd.to_datetime(p["date"])
    pn = p["nonarb_net"].reindex(k.index).fillna(0)
    for n, q in itertools.product([1, 5, 20], [0.5, 0.7]):
        s = pn.rolling(n).sum()
        thr = s.rolling(250).quantile(q)  # 과거 1년 분위(미래정보 없음)
        rows.append(row(f"P1 비차익 {n}일합 > 1년 {int(q*100)}분위", strat(ret, s > thr), base))
        rows.append(row(f"P1+추세 비차익 {n}일합>{int(q*100)}분위 & 60일선 위", strat(ret, (s > thr) & (k > ma * 1.01)), base))

    fpath = os.path.join(C, "flows", "investor_KOSPI.csv")
    if os.path.exists(fpath):
        f = pd.read_csv(fpath, dtype={"date": str}).drop_duplicates("date")
        f.index = pd.to_datetime(f["date"])
        if len(f) > 1000:
            fn = f["foreign"].reindex(k.index).fillna(0)
            ins = f["inst"].reindex(k.index).fillna(0)
            for n in (5, 20, 60):
                rows.append(row(f"F1 외국인 {n}일 누적 순매수>0", strat(ret, fn.rolling(n).sum() > 0), base))
                rows.append(row(f"F1 외국인+기관 {n}일 누적>0", strat(ret, (fn + ins).rolling(n).sum() > 0), base))
                rows.append(row(f"F1+추세 외국인 {n}일>0 & 60일선 위", strat(ret, (fn.rolling(n).sum() > 0) & (k > ma * 1.01)), base))
    df = pd.DataFrame(rows)
    pd.set_option("display.width", 220)
    pd.set_option("display.max_rows", 200)
    print("[두 기간 샤프 우위 — 기준 통과]")
    print(df[df["두기간샤프우위"]].sort_values("샤프", ascending=False).to_string(index=False))
    print("\n[수익 최상위 10 — 낙폭 무관]")
    print(df.sort_values("연", ascending=False).head(10).to_string(index=False))


if __name__ == "__main__":
    main()

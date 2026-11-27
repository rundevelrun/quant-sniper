"""국내 ETF 장중(1분봉) 단타 전략 탐색(연구용, 약 1년 데이터).

전략(모두 당일 청산, 15:15 이전)
  ORB   시가 후 N분 고가 돌파 시 매수, 손절=N분 저가(또는 중간값), 15:15 청산
  GAP   시가 갭이 -g% 이하면 09:0x 매수(되돌림), 지정 시각 청산 / +g% 이상이면 인버스 대응은 별도 상품으로
  MOM   전일 종가→09:30 수익률 부호대로 14:50 매수 → 15:15 청산(장 막판 모멘텀)
  VWAP  10:00 이후 가격이 VWAP 대비 -k% 이하면 매수, VWAP 복귀 또는 15:15 청산
비용: 왕복 0.05%(수수료 0.03% + 1호가), 거래세 없음(ETF).
판정: 앞 6개월 / 뒤 6개월 모두 플러스 + 매매당 평균 순수익 > 0.
"""
import itertools
import os
from multiprocessing import Pool

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
M = os.path.join(ROOT, "backtest", ".cache", "minutes")
COST = 0.0005
NAMES = {"122630": "KODEX 레버리지", "252670": "200선물인버스2X", "069500": "KODEX 200",
         "229200": "코스닥150", "233740": "코스닥150레버리지", "251340": "코스닥150인버스", "114800": "KODEX 인버스"}
_G = {}


def load(code):
    df = pd.read_csv(os.path.join(M, f"{code}.csv"), parse_dates=["time"])
    df = df[(df["time"].dt.time >= pd.Timestamp("09:00").time()) & (df["time"].dt.time <= pd.Timestamp("15:20").time())]
    df["date"] = df["time"].dt.date
    return df


def days(code):
    if code not in _G:
        df = load(code)
        _G[code] = {d: g.reset_index(drop=True) for d, g in df.groupby("date")}
    return _G[code]


def at(g, hhmm):
    t = pd.Timestamp(hhmm).time()
    sub = g[g["time"].dt.time >= t]
    return sub.iloc[0] if len(sub) else None


def orb(code, n, stop_kind):
    trades = []
    for d, g in days(code).items():
        if len(g) < 60:
            continue
        rng = g.iloc[:n]
        hi, lo = rng["high"].max(), rng["low"].min()
        stop = lo if stop_kind == "low" else (hi + lo) / 2
        rest = g.iloc[n:]
        entry = None
        for _, b in rest.iterrows():
            if b["time"].time() >= pd.Timestamp("15:15").time():
                break
            if entry is None and b["high"] > hi:
                entry = max(hi, b["open"])
                continue
            if entry is not None and b["low"] <= stop:
                trades.append((d, stop / entry - 1 - COST))
                entry = None
                break
        if entry is not None:
            ex = at(g, "15:15")
            trades.append((d, (ex["close"] if ex is not None else g.iloc[-1]["close"]) / entry - 1 - COST))
    return trades


def gap(code, g_pct, exit_t, prev_close):
    trades = []
    for d, g in days(code).items():
        pc = prev_close.get(d)
        if pc is None or len(g) < 30:
            continue
        op = g.iloc[0]["open"]
        if op / pc - 1 <= -g_pct:
            e = at(g, "09:05")
            x = at(g, exit_t)
            if e is not None and x is not None:
                trades.append((d, x["close"] / e["close"] - 1 - COST))
    return trades


def mom(code, prev_close):
    trades = []
    for d, g in days(code).items():
        pc = prev_close.get(d)
        a, e, x = at(g, "09:30"), at(g, "14:50"), at(g, "15:15")
        if pc is None or a is None or e is None or x is None:
            continue
        if a["close"] / pc - 1 > 0:
            trades.append((d, x["close"] / e["close"] - 1 - COST))
    return trades


def vwap_rev(code, k):
    trades = []
    for d, g in days(code).items():
        if len(g) < 120:
            continue
        pv = (g["close"] * g["volume"]).cumsum()
        vw = pv / g["volume"].cumsum().replace(0, np.nan)
        entry = None
        for i, b in g.iterrows():
            t = b["time"].time()
            if t < pd.Timestamp("10:00").time():
                continue
            if t >= pd.Timestamp("15:15").time():
                break
            if entry is None and b["close"] <= vw[i] * (1 - k):
                entry = b["close"]
            elif entry is not None and b["close"] >= vw[i]:
                trades.append((d, b["close"] / entry - 1 - COST))
                entry = None
                break
        if entry is not None:
            x = at(g, "15:15")
            trades.append((d, (x["close"] if x is not None else g.iloc[-1]["close"]) / entry - 1 - COST))
    return trades


def prev_closes(code):
    dd = days(code)
    ks = sorted(dd)
    return {ks[i]: dd[ks[i - 1]].iloc[-1]["close"] for i in range(1, len(ks))}


def run(job):
    kind, code, p = job
    pc = prev_closes(code)
    if kind == "ORB":
        tr = orb(code, *p)
    elif kind == "GAP":
        tr = gap(code, *p, pc)
    elif kind == "MOM":
        tr = mom(code, pc)
    else:
        tr = vwap_rev(code, *p)
    if not tr:
        return None
    s = pd.Series([r for _, r in tr], index=pd.to_datetime([d for d, _ in tr]))
    mid = s.index.min() + (s.index.max() - s.index.min()) / 2
    a, b = s[s.index < mid], s[s.index >= mid]
    total = (1 + s).prod() - 1
    eq = (1 + s).cumprod()
    days_n = len(days(code))
    return dict(전략=kind, 상품=NAMES[code], 설정=str(p), 매매=len(s), 승률=round((s > 0).mean() * 100),
                매매당=round(s.mean() * 100, 3), 누적=round(total * 100, 1),
                연환산=round(((1 + total) ** (250 / max(days_n, 1)) - 1) * 100, 1),
                MDD=round((eq / eq.cummax() - 1).min() * 100, 1),
                앞=round(((1 + a).prod() - 1) * 100, 1), 뒤=round(((1 + b).prod() - 1) * 100, 1),
                통과=bool((1 + a).prod() > 1 and (1 + b).prod() > 1 and s.mean() > 0))


def jobs():
    codes = [c for c in NAMES if os.path.exists(os.path.join(M, f"{c}.csv"))]
    out = []
    for c in codes:
        out += [("ORB", c, p) for p in itertools.product([5, 15, 30, 60], ["low", "mid"])]
        out += [("GAP", c, p) for p in itertools.product([0.005, 0.01, 0.02], ["09:30", "10:30", "15:15"])]
        out += [("MOM", c, ())]
        out += [("VWAP", c, (k,)) for k in (0.003, 0.005, 0.01, 0.015)]
    return out


def main():
    with Pool(6) as pool:
        res = [r for r in pool.map(run, jobs()) if r]
    df = pd.DataFrame(res)
    df.to_csv(os.path.join(ROOT, "backtest", ".cache", "intraday_etf_search.csv"), index=False)
    pd.set_option("display.width", 220)
    pd.set_option("display.max_rows", 100)
    print(f"조합 {len(df)}개, 통과 {int(df['통과'].sum())}개")
    print("\n[기준 통과: 앞·뒤 반년 모두 플러스]")
    print(df[df["통과"]].sort_values("연환산", ascending=False).head(15).to_string(index=False))
    print("\n[수익 최상위 10 — 낙폭 무관]")
    print(df.sort_values("연환산", ascending=False).head(10).to_string(index=False))
    print("\n전략별 매매당 평균 순수익 중앙값(%):", df.groupby("전략")["매매당"].median().round(3).to_dict())


if __name__ == "__main__":
    main()

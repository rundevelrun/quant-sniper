"""공시 이벤트 이후 주가 반응(이벤트 스터디).

진입: 공시 접수일 다음 거래일 시가(장 마감 후 공시도 미래정보 없이 반영).
청산: 보유 H거래일 뒤 종가. 시장 대비 초과수익 = 종목 수익 - KODEX 200 같은 구간 수익.
비용: 확정(거래세 0.20% + 수수료 0.03%) 0.23%, 추정 슬리피지 포함 0.73%를 따로 표기.
유동성: 진입 전 20일 평균 거래대금 10억 원 이상, 주가 1,000원 이상.
대상 이벤트
  pead    : 분기·반기·연간 보고서 영업이익의 전년 동기 대비 증감률 상위/하위 20%
  buyback : 자기주식 취득 결정
  supply  : 단일판매·공급계약 체결
한계: 상장폐지 종목이 없어 결과가 실제보다 좋게 나올 수 있다.
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "backtest", ".cache")
HOLDS = (5, 20, 60)
COST_FIXED, COST_EST = 0.0023, 0.0073


def load_prices():
    prices = {}
    for f in os.listdir(os.path.join(CACHE, "stocks")):
        if f.endswith(".csv"):
            df = pd.read_csv(os.path.join(CACHE, "stocks", f), parse_dates=["Date"], index_col="Date")
            df = df[(df["Open"] > 0) & (df["Close"] > 0)]
            df["value20"] = (df["Close"] * df["Volume"]).rolling(20).mean().shift(1)
            prices[f[:-4]] = df
    market = pd.read_csv(os.path.join(CACHE, "069500.csv"), index_col=0, parse_dates=True)
    return prices, market


def event_returns(events, prices, market):
    rows = []
    for ev in events.itertuples():
        df = prices.get(str(ev.stock_code).zfill(6))
        if df is None:
            continue
        i = df.index.searchsorted(pd.Timestamp(ev.date), side="right")  # 다음 거래일
        if i >= len(df) or df["Close"].iloc[i - 1 if i else 0] < 1000 or df["value20"].iloc[i] < 1e9:
            continue
        entry_day, entry = df.index[i], df["Open"].iloc[i]
        row = {"date": entry_day, "group": getattr(ev, "group", ev.kind)}
        for h in HOLDS:
            j = i + h - 1
            if j >= len(df):
                row[h] = np.nan
                continue
            exit_day = df.index[j]
            ret = df["Close"].iloc[j] / entry - 1
            m = market.loc[entry_day:exit_day]
            mret = m["Close"].iloc[-1] / m["Open"].iloc[0] - 1 if len(m) else 0.0
            row[h] = ret
            row[f"ab{h}"] = ret - mret
        rows.append(row)
    return pd.DataFrame(rows)


def summarize(res, label):
    print(f"\n[{label}] 이벤트 {len(res)}건")
    for h in HOLDS:
        r = res[h].dropna()
        ab = res[f"ab{h}"].dropna()
        early = res.loc[res["date"] < "2021-07-01", f"ab{h}"].dropna()
        late = res.loc[res["date"] >= "2021-07-01", f"ab{h}"].dropna()
        print(f"  {h:>2}일 보유: 평균 {r.mean()*100:+.2f}% (비용 후 {(r.mean()-COST_FIXED)*100:+.2f}% / "
              f"{(r.mean()-COST_EST)*100:+.2f}%) 중앙값 {r.median()*100:+.2f}% 승률 {(r>COST_FIXED).mean()*100:.0f}% "
              f"| 시장 대비 {ab.mean()*100:+.2f}% (앞 {early.mean()*100:+.2f}% / 뒤 {late.mean()*100:+.2f}%)")


def pead_events():
    q = pd.read_csv(os.path.join(CACHE, "dart_quarterly.csv"), dtype={"stock_code": str, "rcept_date": str})
    q = q[q["account"] == "영업이익"].dropna(subset=["this", "prev", "rcept_date"])
    q = q.sort_values("fs_div").drop_duplicates(["stock_code", "year", "report"], keep="first")  # CFS 우선
    q = q[q["prev"].abs() > 0]
    q["surprise"] = (q["this"] - q["prev"]) / q["prev"].abs()
    q["date"] = pd.to_datetime(q["rcept_date"], format="%Y%m%d", errors="coerce")
    q = q.dropna(subset=["date"])
    q["rank"] = q.groupby(["year", "report"])["surprise"].rank(pct=True)
    q["group"] = np.where(q["rank"] >= 0.8, "상위20%", np.where(q["rank"] <= 0.2, "하위20%", "중간"))
    q["kind"] = "pead"
    return q[["stock_code", "date", "kind", "group"]]


def main(which):
    prices, market = load_prices()
    if which == "pead":
        ev = pead_events()
        res = event_returns(ev, prices, market)
        for g in ("상위20%", "중간", "하위20%"):
            summarize(res[res["group"] == g], f"실적 발표 후 추세 — 영업이익 증가율 {g}")
    else:
        ev = pd.read_csv(os.path.join(CACHE, "dart_events.csv"), dtype={"stock_code": str, "date": str})
        ev["date"] = pd.to_datetime(ev["date"], format="%Y%m%d")
        for kind, label in (("buyback", "자기주식 취득 결정"), ("supply", "단일판매·공급계약 체결")):
            sub = ev[ev["kind"] == kind].copy()
            if len(sub):
                summarize(event_returns(sub, prices, market), label)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "pead")

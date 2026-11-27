"""국내 대형주 개별 종목 추세 진입·청산 검증(연구용).

종목: 시가총액 상위권 20종목(야후 수정주가, 배당 포함). 생존 편향 있음(현재 대형주만).
신호(전일 종가 → 다음 날): 200일선 위 / 골든크로스(50>200) / 120일 모멘텀 > 0 / 60일선+1%
비용: 확정 0.23%(거래세+수수료) + 추정 슬리피지 0.5%(왕복) = 왕복 0.73%
비교: 종목별 그냥 보유, 그리고 20종목 동일가중 포트폴리오(추세 조건 충족 종목만 보유).
구간: 2003–2014 / 2015–2026.
"""
import json
import os
from multiprocessing import Pool

import numpy as np
import pandas as pd

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", "yahoo")
CODES = {"005930": "삼성전자", "000660": "SK하이닉스", "005380": "현대차", "035420": "NAVER", "000270": "기아",
         "005490": "POSCO홀딩스", "105560": "KB금융", "068270": "셀트리온", "207940": "삼성바이오", "051910": "LG화학",
         "035720": "카카오", "006400": "삼성SDI", "012330": "현대모비스", "055550": "신한지주", "003670": "포스코퓨처엠",
         "017670": "SK텔레콤", "015760": "한국전력", "033780": "KT&G", "028260": "삼성물산", "066570": "LG전자"}
ROUND_TRIP = 0.0073
RULES = {"200일선": ("MA", 200, 0), "골든크로스": ("CROSS", 50, 200), "120일 모멘텀": ("MOM", 120, 0), "60일선+1%": ("MA", 60, 0.01)}


def load(code):
    j = json.load(open(os.path.join(D, f"kr_{code}.json")))["chart"]["result"][0]
    s = pd.Series(j["indicators"]["adjclose"][0]["adjclose"], index=pd.to_datetime(j["timestamp"], unit="s").normalize())
    s = s[~s.index.duplicated()].dropna()
    return s[s > 0]


def signal(x, rule):
    kind, a, b = rule
    if kind == "MA":
        return x > x.rolling(a).mean() * (1 + b)
    if kind == "CROSS":
        return x.rolling(a).mean() > x.rolling(b).mean()
    return x / x.shift(a) - 1 > 0


def stats(r):
    r = r.dropna()
    if len(r) < 250:
        return np.nan, np.nan, np.nan
    e = (1 + r).cumprod()
    return (e.iloc[-1] ** (252 / len(r)) - 1) * 100, (e / e.cummax() - 1).min() * 100, r.mean() / r.std() * np.sqrt(252)


def per_stock(code):
    x = load(code)
    ret = x.pct_change().fillna(0)
    out = {"종목": CODES[code], "보유": stats(ret)}
    for name, rule in RULES.items():
        pos = signal(x, rule).shift(1, fill_value=False).astype(float)
        r = ret * pos - pos.diff().abs().fillna(0) * ROUND_TRIP / 2
        out[name] = (stats(r), stats(r[:"2014"]), stats(r["2015":]), int((pos.diff() > 0).sum()))
    return code, out, ret, {n: signal(x, rl).shift(1, fill_value=False) for n, rl in RULES.items()}


def main():
    with Pool(6) as pool:
        res = pool.map(per_stock, list(CODES))
    pd.set_option("display.width", 220)
    rows = []
    for code, o, _, _ in res:
        row = {"종목": o["종목"], "보유 연": round(o["보유"][0], 1), "보유 MDD": round(o["보유"][1], 1)}
        for n in RULES:
            (f, a, b, trades) = o[n]
            row[f"{n} 연"] = round(f[0], 1)
            row[f"{n} MDD"] = round(f[1], 1)
        rows.append(row)
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    for n in RULES:
        better = ((df[f"{n} 연"] > df["보유 연"]) & (df[f"{n} MDD"] > df["보유 MDD"])).sum()
        print(f"{n}: 수익·낙폭 모두 보유보다 나은 종목 {better}/{len(df)}, 수익 중앙값 {df[f'{n} 연'].median():.1f}% vs 보유 {df['보유 연'].median():.1f}%")

    # 20종목 동일가중: 추세 조건 충족 종목만 보유(나머지 현금), 매일 비중 재조정 근사
    rets = pd.DataFrame({c: r for c, _, r, _ in res}).sort_index()
    print("\n[20종목 포트폴리오, 2003~]")
    bh = rets.loc["2003":].mean(axis=1, skipna=True)
    f, a, b = stats(bh), stats(bh[:"2014"]), stats(bh["2015":])
    print(f"  동일가중 보유        연 {f[0]:.1f}% MDD {f[1]:.1f}% 샤프 {f[2]:.2f} | 03-14 {a[0]:.1f}% 15-26 {b[0]:.1f}%")
    for n in RULES:
        pos = pd.DataFrame({c: s[n] for c, _, _, s in res}).reindex(rets.index).fillna(False).astype(float)
        avail = rets.notna().astype(float)
        w = pos * avail / avail.sum(axis=1).replace(0, np.nan).values[:, None]
        turnover = w.diff().abs().sum(axis=1).fillna(0)
        r = (w * rets.fillna(0)).sum(axis=1) - turnover * ROUND_TRIP / 2
        r = r.loc["2003":]
        f, a, b = stats(r), stats(r[:"2014"]), stats(r["2015":])
        print(f"  {n:12s} 추세 보유 연 {f[0]:.1f}% MDD {f[1]:.1f}% 샤프 {f[2]:.2f} | 03-14 {a[0]:.1f}% 15-26 {b[0]:.1f}% | 평균 투자비중 {pos.mul(avail).sum(axis=1).div(avail.sum(axis=1)).loc['2003':].mean()*100:.0f}%")


if __name__ == "__main__":
    main()

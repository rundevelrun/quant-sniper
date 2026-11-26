"""Stress test: core 50% + rotation 30% with proxy data back to 2007 (incl. 2008).

Proxies (Yahoo daily): KODEX200 actual, S&P500/Nasdaq100 x USDKRW, gold futures (USD, ~hedged),
IEF (US 7-10y Treasury, USD, ~hedged) for Korean bonds, USDKRW for dollar, synthetic -1x KODEX200.
"""
import json, os, sys
import numpy as np, pandas as pd

D = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", "yahoo")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import etf_robustness as er  # noqa: E402


def load(name):
    j = json.load(open(os.path.join(D, name)))["chart"]["result"][0]
    q = j["indicators"]["quote"][0]
    idx = pd.to_datetime(j["timestamp"], unit="s").normalize()
    adj = j["indicators"].get("adjclose", [{}])[0].get("adjclose")
    df = pd.DataFrame({k.capitalize(): q[k] for k in ("open", "high", "low", "close")}, index=idx)
    if adj:  # scale OHLC to adjusted close (dividends)
        f = pd.Series(adj, index=idx) / df["Close"]
        df = df.mul(f, axis=0)
    df = df[~df.index.duplicated(keep="last")].dropna(how="all")
    return df


_kd = load("069500.KS.json").dropna()
_ks = load("_KS11.json").dropna()
_splice = pd.Timestamp("2009-07-01")
_ratio = _kd.loc[_kd.index >= _splice, "Close"].iloc[0] / _ks.loc[_ks.index >= _splice, "Close"].iloc[0]
k200 = pd.concat([_ks[_ks.index < _splice] * _ratio, _kd[_kd.index >= _splice]])
k200 = k200[~k200.index.duplicated(keep="last")]
dates = k200.index[k200.index >= "2007-02-01"]
fx = load("KRW_X.json")["Close"].reindex(dates, method="ffill")


def usd_krw(df):
    df = df.reindex(dates, method="ffill")
    return df.mul(fx, axis=0)


def inverse(df):
    df = df.reindex(dates)
    prev = df["Close"].shift(1)
    inv_close, out = [1000.0], {"Open": [np.nan], "High": [np.nan], "Low": [np.nan], "Close": [1000.0]}
    for i in range(1, len(df)):
        p = inv_close[-1]
        pc = prev.iloc[i]
        o, h, l, c = (df[k].iloc[i] for k in ("Open", "High", "Low", "Close"))
        nc = p * (1 - (c / pc - 1))
        out["Open"].append(p * (1 - (o / pc - 1)))
        out["High"].append(p * (1 - (l / pc - 1)))
        out["Low"].append(p * (1 - (h / pc - 1)))
        out["Close"].append(nc)
        inv_close.append(nc)
    return pd.DataFrame(out, index=df.index)


frames = {
    "069500": k200.reindex(dates),
    "379800": usd_krw(load("_GSPC.json")),
    "379810": usd_krw(load("_NDX.json")),
    "132030": load("GC_F.json").reindex(dates, method="ffill"),
    "114260": load("IEF.json").reindex(dates, method="ffill"),
    "261240": pd.DataFrame({k: fx for k in ("Open", "High", "Low", "Close")}),
    "114800": inverse(k200),
}
arr = {c: {k: df[k].values.astype(float) for k in ("Open", "High", "Low", "Close")} for c, df in frames.items()}
start_i = int(np.searchsorted(dates, pd.Timestamp("2007-09-03")))
eq, _ = er.simulate(dates, arr, start_i, alloc=1.0, assets=list(arr), **{k: v for k, v in er.BASE.items()})
trend = pd.Series(eq, index=dates).pct_change().fillna(0)

# core: stocks 30 (K200 15 + NDX 15), bonds 40, gold 15, usd 15, monthly rebalance
closes = pd.DataFrame({
    "k": frames["069500"]["Close"], "nq": frames["379810"]["Close"], "b": frames["114260"]["Close"],
    "g": frames["132030"]["Close"], "u": frames["261240"]["Close"],
}).ffill()
w = {"k": .15, "nq": .15, "b": .40, "g": .15, "u": .15}
rets = closes.pct_change().fillna(0)
core_r, hold = [], dict(w)
month = None
for d, r in rets.iterrows():
    if d.month != month:
        hold, month = dict(w), d.month
    tot = sum(hold.values())
    day = sum(hold[a] * r[a] for a in hold) / tot
    hold = {a: hold[a] * (1 + r[a]) for a in hold}
    core_r.append(day)
core = pd.Series(core_r, index=dates)

combo = 0.5 * core + 0.3 * trend
combo = combo[dates >= "2007-09-03"]
coreonly = core[dates >= "2007-09-03"]
k2 = rets["k"][dates >= "2007-09-03"]


def stats(r, name):
    eq = (1 + r).cumprod()
    yrs = len(r) / 252
    dd = eq / eq.cummax() - 1
    yr = (1 + r).groupby(r.index.year).prod() - 1
    print(f"{name}: 연{(eq.iloc[-1] ** (1 / yrs) - 1) * 100:.1f}% MDD {dd.min() * 100:.1f}% "
          f"(바닥 {dd.idxmin().date()}) 최악일 {r.min() * 100:.1f}% ({r.idxmin().date()}) 최악해 {yr.min() * 100:.1f}%({yr.idxmin()})")
    return yr, dd


for name, r in (("코어50+추세30", combo), ("코어100", coreonly), ("KODEX200", k2)):
    stats(r, name)

print("\n구간별 수익률(코어50+추세30 / 코어100 / KODEX200)")
for a, b, lab in (("2008-01-01", "2008-12-31", "2008"), ("2008-05-15", "2009-03-01", "2008 고점→바닥"),
                  ("2011-07-01", "2011-10-31", "2011 유럽위기"), ("2020-02-15", "2020-03-31", "2020 코로나"),
                  ("2022-01-01", "2022-12-31", "2022 주식채권 동반하락"), ("2024-07-30", "2024-08-09", "2024-08 폭락"),
                  ("2017-01-01", "2026-12-31", "2017- (실제 백테스트 구간)")):
    out = []
    for r in (combo, coreonly, k2):
        s = r[(r.index >= a) & (r.index <= b)]
        out.append(f"{((1 + s).prod() - 1) * 100:+.1f}%")
    print(f"  {lab}: " + " / ".join(out))

yr, dd = stats(combo, "check")
print("\n연도별(코어50+추세30):", " ".join(f"{y}:{v*100:+.0f}%" for y, v in yr.items()))

"""새 전략 후보 전수 탐색(기존에 검증·기각한 계열 제외).

계열
  MR   ETF 단기 평균회귀: RSI(n) < 기준 & 추세 필터 → 다음 날 시가 매수, 청산 조건 → 다음 날 시가 매도
  TOM  월말·월초 효과: 월말 k거래일 전 종가 매수 → 다음 달 m번째 거래일 종가 매도
  VT   변동성 목표: 현재 포트폴리오(코어 50 + 추세 30)의 위험 노출을 목표 변동성에 맞춰 축소
  REL  코스피200 ↔ 코스닥150 상대 모멘텀 전환(절대 모멘텀 필터 선택)
  QG   개별주 실적 성장(DART 분기 영업이익 YoY) × 모멘텀/저변동성, 월 1회 상위 N
  ON   ETF 오버나잇(종가 매수 → 다음 날 시가 매도)

판정(과적합 방지): 2017–2021, 2022–2026 두 기간 모두
  (1) 단독 연수익 > 대기자금 수준(연 2.5%) 그리고
  (2) 현재 포트폴리오의 현금 10%를 이 전략으로 바꿨을 때 샤프 개선.
비용: ETF 편도 0.065%, 해외·금·달러 ETF 매매차익 15.4%. 개별주 매수 0.265%·매도 0.465%.
"""
import itertools
import os
import sys
from multiprocessing import Pool

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backtest"))
from etf_rotation_backtest import load_prices  # noqa: E402

CACHE = os.path.join(ROOT, "backtest", ".cache")
START = pd.Timestamp("2017-01-02")
HALVES = (("2017", "2021"), ("2022", "2026"))
ETF_ONE_WAY = 0.00065
TAX_FREE = {"069500", "229200"}
CASH_YIELD = 0.025
MR_ETFS = ["069500", "229200", "133690", "132030", "261240"]
_G = {}


# ---------------------------------------------------------------- 공통
def stats(r):
    r = r.dropna()
    if len(r) < 60 or r.std() == 0:
        return np.nan, np.nan, np.nan
    eq = (1 + r).cumprod()
    cagr = eq.iloc[-1] ** (252 / len(r)) - 1
    return cagr * 100, (eq / eq.cummax() - 1).min() * 100, r.mean() / r.std() * np.sqrt(252)


def load_etfs():
    codes = sorted(set(MR_ETFS) | {"148070", "114800", "357870"})
    data = load_prices(codes, "2014-01-01", CACHE)
    idx = data["069500"].index
    o = pd.DataFrame({c: d["Open"] for c, d in data.items()}).reindex(idx).ffill()
    c = pd.DataFrame({c: d["Close"] for c, d in data.items()}).reindex(idx).ffill()
    return o, c


def baseline_returns(close):
    """현재 포트폴리오(코어 50 + 추세 30, 나머지 현금) 일간 수익."""
    import etf_robustness as er
    import static_allocation as sa
    dates, arr, si = er.prepare()
    eq, _ = er.simulate(dates, arr, si, alloc=1.0, **er.BASE)
    trend = eq.pct_change().fillna(0)
    core_w = {"069500": .15, "133690": .15, "148070": .40, "132030": .15, "261240": .15}
    core = sa.simulate(close[list(core_w)], core_w, START).pct_change().fillna(0)
    idx = trend.index.intersection(core.index)
    return 0.5 * core.reindex(idx) + 0.3 * trend.reindex(idx)


def judge(sleeve, base):
    idx = base.index
    s = sleeve.reindex(idx).fillna(0)
    cash = pd.Series(CASH_YIELD / 252, index=idx)
    out = {}
    ok = True
    for a, b in HALVES:
        sc = stats(s[a:b])[0]
        with_cash = stats((base + 0.1 * cash)[a:b])
        with_s = stats((base + 0.1 * s)[a:b])
        out[f"{a}단독"] = round(sc, 1)
        out[f"{a}샤프"] = round(with_s[2] - with_cash[2], 3)
        ok &= sc > CASH_YIELD * 100 and with_s[2] > with_cash[2]
    full = stats(s)
    out.update(단독연=round(full[0], 1), 단독MDD=round(full[1], 1), 통과=ok)
    return out


# ---------------------------------------------------------------- MR
def rsi(close, n):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def mr(args):
    code, n, thr, ma, exit_rule = args
    o, c = _G["o"][code], _G["c"][code]
    r_ = rsi(c, n)
    trend = c > c.rolling(ma).mean() if ma else pd.Series(True, index=c.index)
    ma5 = c.rolling(5).mean()
    ret = pd.Series(0.0, index=c.index)
    held, entry_px, entry_i = False, 0.0, 0
    idx = c.index
    for i in range(1, len(idx) - 1):
        if held:
            done = (exit_rule == "rsi70" and r_.iloc[i] > 70) or \
                   (exit_rule == "ma5" and c.iloc[i] > ma5.iloc[i]) or \
                   (isinstance(exit_rule, int) and i - entry_i >= exit_rule)
            if done:
                px = o.iloc[i + 1]
                ret.iloc[i + 1] += px / c.iloc[i] - 1  # 마지막 날 종가→시가
                gain = px * (1 - ETF_ONE_WAY) / (entry_px * (1 + ETF_ONE_WAY)) - 1
                cost = 2 * ETF_ONE_WAY + (0.154 * gain if code not in TAX_FREE and gain > 0 else 0)
                ret.iloc[i + 1] -= cost
                held = False
                continue
            ret.iloc[i + 1] += c.iloc[i + 1] / c.iloc[i] - 1
        elif r_.iloc[i] < thr and trend.iloc[i]:
            held, entry_px, entry_i = True, o.iloc[i + 1], i + 1
            ret.iloc[i + 1] += c.iloc[i + 1] / o.iloc[i + 1] - 1
    ret = ret[ret.index >= START]
    return ("MR", f"{code} RSI{n}<{thr} MA{ma} 청산{exit_rule}"), ret


# ---------------------------------------------------------------- TOM
def tom(args):
    code, k, m = args
    c = _G["c"][code]
    idx = c.index
    month = pd.Series(idx.to_period("M"), index=idx)
    pos_from_end = month.groupby(month).cumcount(ascending=False)  # 0 = 월말 마지막 날
    pos_from_start = month.groupby(month).cumcount()               # 0 = 월초 첫날
    hold = ((pos_from_end < k) | (pos_from_start < m)).shift(1, fill_value=False)
    r = c.pct_change().fillna(0) * hold
    entries = hold & ~hold.shift(1, fill_value=False)
    r -= entries * 2 * ETF_ONE_WAY
    return ("TOM", f"{code} 월말{k}일+월초{m}일"), r[r.index >= START]


# ---------------------------------------------------------------- REL
def rel(args):
    look, absf, freq = args
    c = _G["c"][["069500", "229200"]]
    mom = c / c.shift(look) - 1
    dates = c.index
    reb = pd.Series(dates, index=dates).groupby(
        [dates.year, dates.month] if freq == "M" else [dates.year, dates.isocalendar().week]
    ).last().values
    choice = pd.Series(np.nan, index=dates, dtype=object)
    for d in reb:
        m = mom.loc[d].dropna()
        if m.empty:
            continue
        best = m.idxmax()
        choice.loc[d] = best if (not absf or m[best] > 0) else "cash"
    choice = choice.ffill().shift(1)
    rets = c.pct_change().fillna(0)
    r = pd.Series([rets.loc[d, ch] if isinstance(ch, str) and ch in rets.columns else 0.0
                   for d, ch in choice.items()], index=dates)
    switches = (choice != choice.shift(1)) & choice.notna()
    r -= switches * 2 * ETF_ONE_WAY
    return ("REL", f"룩백{look} 절대필터{absf} {freq}"), r[r.index >= START]


# ---------------------------------------------------------------- ON
def overnight(args):
    code, = args
    o, c = _G["o"][code], _G["c"][code]
    r = o / c.shift(1) - 1 - 2 * ETF_ONE_WAY
    return ("ON", f"{code} 종가→시가"), r[r.index >= START].fillna(0)


# ---------------------------------------------------------------- VT
def vt(args, base):
    target, look = args
    vol = base.rolling(look).std().shift(1) * np.sqrt(252)
    scale = (target / vol).clip(upper=1.0).fillna(1.0)
    # 줄인 노출분은 현금(대기자금 수익)으로: 슬리브 = 조정 포트폴리오 - 기준
    adj = base * scale + (1 - scale) * 0.8 * CASH_YIELD / 252
    turnover = scale.diff().abs().fillna(0) * 0.8 * ETF_ONE_WAY
    return ("VT", f"목표변동성{target*100:.0f}% 룩백{look}"), (adj - turnover - base) / 0.1


# ---------------------------------------------------------------- QG (개별주)
def qg_panels():
    from stock_factor_research import load_stocks  # noqa: F401  (경로 확인용)
    q = pd.read_csv(os.path.join(CACHE, "dart_quarterly.csv"), dtype={"stock_code": str, "rcept_date": str})
    q = q[q["account"] == "영업이익"].dropna(subset=["this", "prev", "rcept_date"])
    q = q.sort_values("fs_div").drop_duplicates(["stock_code", "year", "report"], keep="first")
    q = q[q["prev"].abs() > 0]
    q["g"] = (q["this"] - q["prev"]) / q["prev"].abs()
    q["pos"] = q["this"] > 0
    q["date"] = pd.to_datetime(q["rcept_date"], format="%Y%m%d", errors="coerce")
    return q.dropna(subset=["date"])


def qg(args):
    score_kind, top_n, mkt_filter = args
    close, value20, growth, positive, mom, lowvol, mkt_ok = (
        _G[k] for k in ("s_close", "s_value", "s_g", "s_pos", "s_mom", "s_lv", "s_mkt"))
    elig = (close >= 1000) & (value20 >= 1e9) & positive.fillna(False).astype(bool) & growth.notna()
    rk = lambda x: x.rank(axis=1, pct=True)
    score = {"성장": rk(growth), "성장+모멘텀": rk(growth) + rk(mom), "성장+저변동": rk(growth) + rk(lowvol)}[score_kind]
    dates = close.index
    month_end = set(pd.DatetimeIndex(pd.Series(dates, index=dates).groupby([dates.year, dates.month]).last().values))
    ret = close.pct_change(fill_method=None).fillna(0)
    w = pd.Series(0.0, index=close.columns)
    out, pending = [], None
    for d in dates:
        cost = 0.0
        if pending is not None:
            diff = pending - w
            cost = diff.clip(lower=0).sum() * 0.00265 + (-diff.clip(upper=0)).sum() * 0.00465
            w, pending = pending, None
        out.append((w * ret.loc[d]).sum() - cost)
        if d in month_end:
            s = score.loc[d].where(elig.loc[d]).dropna()
            new = pd.Series(0.0, index=close.columns)
            if len(s) and (not mkt_filter or mkt_ok.loc[d]):
                picks = s.sort_values(ascending=False).index[:top_n]
                new[picks] = 1.0 / len(picks)
            pending = new
    r = pd.Series(out, index=dates)
    return ("QG", f"{score_kind} 상위{top_n} 시장필터{mkt_filter}"), r[r.index >= START]


def init_qg():
    frames = {}
    for f in os.listdir(os.path.join(CACHE, "stocks")):
        if f.endswith(".csv"):
            df = pd.read_csv(os.path.join(CACHE, "stocks", f), parse_dates=["Date"], index_col="Date")
            frames[f[:-4]] = df
    close = pd.DataFrame({k: v["Close"] for k, v in frames.items()}).sort_index()
    vol = pd.DataFrame({k: v["Volume"] for k, v in frames.items()}).reindex(close.index)
    close = close[close.index >= "2015-06-01"]
    vol = vol.reindex(close.index)
    q = qg_panels()
    growth = pd.DataFrame(index=close.index, columns=close.columns, dtype=float)
    positive = pd.DataFrame(index=close.index, columns=close.columns, dtype=float)
    for code, g in q.groupby("stock_code"):
        if code in growth.columns:
            s = g.sort_values("date").drop_duplicates("date", keep="last").set_index("date")
            growth[code] = s["g"].reindex(close.index, method="ffill")
            positive[code] = s["pos"].astype(float).reindex(close.index, method="ffill")
    ret = close.pct_change(fill_method=None)
    k200 = _G["c"]["069500"].reindex(close.index).ffill()
    _G.update(
        s_close=close, s_value=(close * vol).rolling(20).mean(), s_g=growth, s_pos=positive.astype(bool),
        s_mom=close.shift(21) / close.shift(126) - 1, s_lv=-ret.rolling(120).std(),
        s_mkt=k200 > k200.rolling(120).mean(),
    )


def _init(o, c, with_qg):
    _G["o"], _G["c"] = o, c
    if with_qg:
        init_qg()


def main():
    o, c = load_etfs()
    base = baseline_returns(c)
    jobs_fast = (
        [(mr, a) for a in itertools.product(MR_ETFS, [2, 3], [5, 10, 15, 20, 25], [0, 100, 200],
                                            ["rsi70", "ma5", 3, 5, 10])]
        + [(tom, a) for a in itertools.product(MR_ETFS, [1, 2, 3, 4], [1, 2, 3, 5])]
        + [(rel, a) for a in itertools.product([20, 40, 60, 120], [False, True], ["W", "M"])]
        + [(overnight, (code,)) for code in MR_ETFS]
    )
    jobs_qg = [(qg, a) for a in itertools.product(["성장", "성장+모멘텀", "성장+저변동"], [10, 20, 30], [False, True])]
    results = []
    with Pool(6, initializer=_init, initargs=(o, c, False)) as pool:
        for item in pool.starmap(_call, jobs_fast):
            if item is None:
                continue
            (fam, name), r = item
            results.append({"계열": fam, "설정": name, **judge(r, base)})
    with Pool(3, initializer=_init, initargs=(o, c, True)) as pool:
        for item in pool.starmap(_call, jobs_qg):
            if item is None:
                continue
            (fam, name), r = item
            results.append({"계열": fam, "설정": name, **judge(r, base)})
    for a in itertools.product([0.05, 0.07, 0.09, 0.11], [20, 60]):
        (fam, name), r = vt(a, base)
        adj = base + 0.1 * r  # vt 슬리브는 (조정 포트폴리오 - 기준)/0.1
        row = {"계열": fam, "설정": name}
        ok = True
        for x, y in HALVES:
            b_, a_ = stats(base[x:y]), stats(adj[x:y])
            row[f"{x}단독"] = round(a_[0] - b_[0], 2)   # 연수익 변화(%p)
            row[f"{x}샤프"] = round(a_[2] - b_[2], 3)
            ok &= a_[2] > b_[2]
        f_b, f_a = stats(base), stats(adj)
        row.update(단독연=round(f_a[0] - f_b[0], 2), 단독MDD=round(f_a[1] - f_b[1], 1), 통과=ok)
        results.append(row)
    df = pd.DataFrame(results)
    df.to_csv(os.path.join(CACHE, "strategy_search.csv"), index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 200)
    print("탐색 조합 수:", len(df), " 계열별:", df.groupby("계열").size().to_dict())
    print("계열별 통과 비율(%):", (df.groupby("계열")["통과"].mean() * 100).round(0).to_dict())
    passed = df[df["통과"]].sort_values(["2017샤프", "2022샤프"], ascending=False)
    print("\n통과 조합 상위 25:")
    print(passed.head(25).to_string(index=False))


def _call(fn, args):
    try:
        return fn(args)
    except Exception as exc:  # 한 조합의 오류가 전체 탐색을 멈추지 않게 한다
        print("조합 오류", fn.__name__, args, exc, flush=True)
        return None


if __name__ == "__main__":
    main()

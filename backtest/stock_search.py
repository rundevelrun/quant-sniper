"""개별주 전략 조건 탐색(그리드). 2017~2021로 고르고 2022~2026으로 검증한다.

주의: 조합을 많이 돌릴수록 우연히 좋아 보이는 결과가 나온다. 표본 내 상위 조합이
표본 외에서도 유지되는지만 근거로 삼는다. 현재 상장 종목만 있어 생존편향이 있다.
"""
import datetime
import itertools
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backtest"))
import bot  # noqa: E402
from config import ETF_ROTATION_ASSETS, ETF_ROTATION_INVERSE_CODE  # noqa: E402
from full_backtest import load_stocks, CACHE, candles_upto  # noqa: E402
from etf_rotation_backtest import load_prices  # noqa: E402

BUY_COST, SELL_COST = 0.00265, 0.00465
IS = ("2017-01-01", "2021-12-31")
OOS = ("2022-01-01", "2026-12-31")


def rotation_targets(etf, dates):
    path = os.path.join(CACHE, "rotation_targets.csv")
    if os.path.exists(path):
        s = pd.read_csv(path, index_col=0, parse_dates=True, dtype=str)["target"]
        if len(s) == len(dates):
            return s.reindex(dates)
    held, out = None, []
    frames = {c: etf[c] for c in ETF_ROTATION_ASSETS}
    for d in dates:
        completed = {c: candles_upto(f, d, 130, include_today=False) for c, f in frames.items()}
        _, target, _ = bot.multi_asset_trend_selection(
            completed, held, datetime.datetime(d.year, d.month, d.day, 9, 15))
        held = target
        out.append(target or "")
    s = pd.Series(out, index=dates, name="target")
    s.to_frame().to_csv(path)
    return s


def run(score, ret, rebal_idx, gate, n):
    T, S = score.shape
    W = np.zeros((T, S))
    for k, i in enumerate(rebal_idx):
        row = score[i]
        valid = np.flatnonzero(~np.isnan(row))
        j = rebal_idx[k + 1] if k + 1 < len(rebal_idx) else T
        if len(valid):
            top = valid[np.argsort(-row[valid])[:n]]
            W[i:j, top] = 1.0 / n
    W *= gate[:, None]
    Wx = np.vstack([np.zeros((1, S)), W[:-1]])          # 다음 날 종가 체결
    held = np.vstack([np.zeros((1, S)), Wx[:-1]])        # 체결 다음 날부터 수익
    gross = (held * ret).sum(axis=1)
    delta = np.diff(Wx, axis=0, prepend=np.zeros((1, S)))
    cost = np.clip(delta, 0, None).sum(axis=1) * BUY_COST + np.clip(-delta, 0, None).sum(axis=1) * SELL_COST
    return gross - cost, np.abs(delta).sum() / 2


def stats(net, mask):
    r = net[mask]
    eq = np.cumprod(1 + r)
    years = len(r) / 252
    cagr = eq[-1] ** (1 / years) - 1
    mdd = (eq / np.maximum.accumulate(eq) - 1).min()
    sharpe = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0.0
    return round(cagr * 100, 1), round(mdd * 100, 1), round(sharpe, 2)


def main():
    codes = list(ETF_ROTATION_ASSETS)
    etf = load_prices(codes, "2014-01-01", CACHE)
    dates = etf["069500"].index
    st = load_stocks(dates)
    close, volume, cap = st["close"], st["volume"], st["cap"]
    ret = close.pct_change(fill_method=None).fillna(0).values
    value20 = (close * volume).rolling(20).mean()
    k = etf["069500"]["Close"].reindex(dates)
    target = rotation_targets(etf, dates)
    print("데이터 준비 완료", close.shape, flush=True)

    rank = lambda x: x.rank(axis=1, pct=True)
    hi252 = close / close.rolling(252).max()
    mom6 = close.shift(21) / close.shift(126) - 1
    factors = {
        "52주고가": hi252,
        "120일고가": close / close.rolling(120).max(),
        "모멘텀6-1": mom6,
        "모멘텀12-1": close.shift(21) / close.shift(252) - 1,
        "모멘텀3": close / close.shift(63) - 1,
        "52주고가+모멘텀6": rank(hi252) + rank(mom6),
    }
    universes = {
        "5천억~5조": (cap >= 5e11) & (cap <= 5e12),
        "1조~20조": (cap >= 1e12) & (cap <= 2e13),
        "2천억~2조": (cap >= 2e11) & (cap <= 2e12),
    }
    liquid = (value20 >= 1e9) & close.notna()
    stock_filters = {
        "없음": pd.DataFrame(True, index=close.index, columns=close.columns),
        "종목>120일선": close > close.rolling(120).mean(),
        "종목>60일선": close > close.rolling(60).mean(),
    }
    price_caps = {"가격제한없음": None, "3만원이하": 30000}
    gates = {
        "없음": np.ones(len(dates)),
        "K200>120일선": (k > k.rolling(120).mean()).values.astype(float),
        "K200>60일선": (k > k.rolling(60).mean()).values.astype(float),
        "ETF국면=국내주식": target.isin(["069500", "229200"]).values.astype(float),
        "ETF국면≠인버스·현금": (~target.isin([ETF_ROTATION_INVERSE_CODE, ""])).values.astype(float),
    }
    ds = pd.Series(dates, index=dates)
    rebal = {
        "월": np.flatnonzero(dates.isin(ds.groupby([dates.year, dates.month]).last().values)),
        "주": np.flatnonzero(dates.isin(ds.groupby([dates.isocalendar().year.values, dates.isocalendar().week.values]).last().values)),
    }
    is_mask = (dates >= IS[0]) & (dates <= IS[1])
    oos_mask = (dates >= OOS[0]) & (dates <= OOS[1])

    rows = []
    for (fn, f), (un, u), (sn, sf), (pn, pc) in itertools.product(
            factors.items(), universes.items(), stock_filters.items(), price_caps.items()):
        elig = u & liquid & sf
        if pc:
            elig &= close <= pc
        score = f.where(elig).values
        for (gn, g), (rn, ri), n in itertools.product(gates.items(), rebal.items(), (5, 10)):
            net, turn = run(score, ret, ri, g, n)
            a, b = stats(net, is_mask), stats(net, oos_mask)
            rows.append({
                "factor": fn, "universe": un, "stock_filter": sn, "price": pn,
                "gate": gn, "rebal": rn, "N": n,
                "IS_cagr": a[0], "IS_mdd": a[1], "IS_sharpe": a[2],
                "OOS_cagr": b[0], "OOS_mdd": b[1], "OOS_sharpe": b[2],
                "turnover_yr": round(turn / (len(dates) / 252), 1),
            })
        print(fn, un, sn, pn, len(rows), flush=True)
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(CACHE, "stock_search.csv"), index=False)
    print("완료", len(out))


if __name__ == "__main__":
    main()

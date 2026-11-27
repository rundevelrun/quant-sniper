"""탐색 3차: 달력·이벤트·짝 거래·외국인 수급(연구용, 일봉).

C1 요일: 특정 요일 하루만 보유(전일 종가 매수 → 당일 종가 매도)
C2 명절: 설·추석 연휴 직전 k거래일 매수 → 연휴 후 m거래일 매도(연휴 = 평일 3일 이상 거래 공백)
C3 옵션 만기(매월 둘째 목요일): 만기 주 보유 / 만기 다음 주 보유
C4 코스피200 정기 변경(6·12월 둘째 목요일 다음 날): 전후 구간 보유
P1 짝 거래: 로그 가격비의 60일 z-점수가 ±z를 넘으면 약한 쪽 매수·강한 쪽 미보유(롱 온리), 0 복귀 시 청산
F1 외국인: 코스피 외국인 N일 누적 순매수>0 이면 KODEX 200 보유(+60일선 조건 변형)
비용: ETF 편도 0.065%. 판정: 2015–2020 / 2021–2026 두 기간 모두 기준 대비 샤프 우위.
"""
import itertools
import os

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
C = os.path.join(ROOT, "backtest", ".cache")
ONE_WAY = 0.00065


def px(code):
    p = os.path.join(C, f"{code}.csv")
    if not os.path.exists(p):
        p = os.path.join(C, "etf_universe", f"{code}.csv")
    return pd.read_csv(p, index_col=0, parse_dates=True)["Close"]


def st(r):
    r = r.dropna()
    if len(r) < 200 or r.std() == 0:
        return (np.nan,) * 3
    e = (1 + r).cumprod()
    return (e.iloc[-1] ** (252 / len(r)) - 1) * 100, (e / e.cummax() - 1).min() * 100, r.mean() / r.std() * np.sqrt(252)


def strat(ret, hold):
    hold = hold.reindex(ret.index).fillna(False).astype(bool)
    sw = hold != hold.shift(1, fill_value=False)
    return ret.where(hold, 0.0) - sw * ONE_WAY


ROWS = []


def rec(name, r, base):
    a, b, f = st(r["2015":"2020"]), st(r["2021":]), st(r["2015":])
    ba, bb = st(base["2015":"2020"]), st(base["2021":])
    ROWS.append(dict(가설=name, 앞=round(a[0], 1), 뒤=round(b[0], 1), 연=round(f[0], 1), MDD=round(f[1], 1),
                     샤프=round(f[2], 2), 보유=round(float(r.ne(0).mean()) * 100),
                     통과=bool(a[2] > ba[2] and b[2] > bb[2])))


def main():
    k = px("069500")
    ret = k.pct_change().fillna(0)
    idx = ret.index
    rec("KODEX 200 보유(기준)", ret, ret)
    # C1 요일
    for wd, nm in enumerate(["월", "화", "수", "목", "금"]):
        rec(f"C1 {nm}요일만 보유", strat(ret, pd.Series(idx.weekday == wd, index=idx)), ret)
    # C2 명절: 거래 공백 3일 이상(주말 포함 5일 이상 달력 공백)
    gaps = pd.Series(idx[1:] - idx[:-1], index=idx[1:])
    after_holiday = gaps[gaps >= pd.Timedelta(days=4)].index  # 연휴 직후 첫 거래일
    pos = pd.Series(np.arange(len(idx)), index=idx)
    for kb, ma in itertools.product([1, 3, 5], [1, 3, 5]):
        hold = pd.Series(False, index=idx)
        for d in after_holiday:
            i = pos[d]
            hold.iloc[max(0, i - kb):i + ma] = True  # 직전 kb일 보유 시작 → 연휴 후 ma일 보유
        rec(f"C2 연휴 전 {kb}일~후 {ma}일 보유", strat(ret, hold.shift(1, fill_value=False) | hold), ret)
    # C3 옵션 만기(둘째 목요일)
    thursdays = idx[idx.weekday == 3]
    expiry = pd.Series(thursdays).groupby([thursdays.year, thursdays.month]).nth(1).values
    expiry = pd.DatetimeIndex(expiry)
    week = idx.to_period("W")
    exp_weeks = set(pd.DatetimeIndex(expiry).to_period("W"))
    next_weeks = {w + 1 for w in exp_weeks}
    rec("C3 만기 주 보유", strat(ret, pd.Series([w in exp_weeks for w in week], index=idx)), ret)
    rec("C3 만기 다음 주 보유", strat(ret, pd.Series([w in next_weeks for w in week], index=idx)), ret)
    # C4 정기 변경(6·12월 만기 다음 날)
    reb = [d for d in expiry if d.month in (6, 12)]
    for before, after in [(10, 0), (5, 5), (0, 10), (0, 20)]:
        hold = pd.Series(False, index=idx)
        for d in reb:
            i = pos[d]
            hold.iloc[max(0, i - before):i + after + 1] = True
        rec(f"C4 정기변경 전 {before}일~후 {after}일", strat(ret, hold), ret)
    # P1 짝 거래(롱 온리)
    for (a_code, b_code, nm), (look, z) in itertools.product(
            [("069500", "229200", "코스피↔코스닥"), ("132030", "261240", "금↔달러"), ("069500", "114260", "주식↔채권")],
            [(60, 1.5), (60, 2.0), (120, 2.0)]):
        A, B = px(a_code).reindex(idx).ffill(), px(b_code).reindex(idx).ffill()
        lr = np.log(A / B)
        zs = (lr - lr.rolling(look).mean()) / lr.rolling(look).std()
        state, wa, wb = 0, [], []
        for v in zs.shift(1).fillna(0).values:
            if state == 0 and v <= -z:
                state = 1  # A가 상대적으로 쌈 → A 보유
            elif state == 0 and v >= z:
                state = -1  # B 보유
            elif (state == 1 and v >= 0) or (state == -1 and v <= 0):
                state = 0
            wa.append(state == 1)
            wb.append(state == -1)
        ra, rb = A.pct_change().fillna(0), B.pct_change().fillna(0)
        r = strat(ra, pd.Series(wa, index=idx)) + strat(rb, pd.Series(wb, index=idx))
        rec(f"P1 {nm} {look}일 z±{z}", r, ret)
    # F1 외국인
    f = pd.read_csv(os.path.join(C, "flows", "investor_KOSPI.csv"), dtype={"date": str}).drop_duplicates("date")
    f.index = pd.to_datetime(f["date"])
    fn = f["foreign"].reindex(idx).fillna(0)
    ins = f["inst"].reindex(idx).fillna(0)
    ma = k.rolling(60).mean()
    for n in (1, 5, 20, 60):
        s = fn.rolling(n).sum().shift(1)
        rec(f"F1 외국인 {n}일 누적>0", strat(ret, s > 0), ret)
        rec(f"F1 외국인+기관 {n}일>0", strat(ret, (fn + ins).rolling(n).sum().shift(1) > 0), ret)
        rec(f"F1 외국인 {n}일>0 & 60일선 위", strat(ret, (s > 0) & (k.shift(1) > ma.shift(1) * 1.01)), ret)
        rec(f"F1 외국인 {n}일<0 역발상", strat(ret, s < 0), ret)
    df = pd.DataFrame(ROWS)
    pd.set_option("display.width", 220)
    pd.set_option("display.max_rows", 200)
    print(f"가설 {len(df)-1}개, 통과 {int(df['통과'].sum())-0}개")
    print("\n[기준 통과]")
    print(df[df["통과"]].sort_values("샤프", ascending=False).to_string(index=False))
    print("\n[수익 최상위 10 — 낙폭 무관]")
    print(df.sort_values("연", ascending=False).head(10).to_string(index=False))


if __name__ == "__main__":
    main()

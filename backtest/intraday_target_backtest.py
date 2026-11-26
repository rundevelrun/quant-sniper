"""당일 단타(익절/손절 목표) 기계적 백테스트.

규칙
- 진입: 당일 시가 매수(신호는 전일까지 확정된 정보 + 당일 시가만 사용)
- 청산: 장중 고가가 목표가(+T%)에 닿으면 익절, 저가가 손절가(-S%)에 닿으면 손절,
  둘 다 아니면 종가 청산. 일봉만 있으므로 같은 날 둘 다 닿으면 순서를 알 수 없어
  보수적(손절 먼저)·낙관적(익절 먼저) 두 경우를 모두 계산한다.
- 비용: 왕복 0.5% (거래세 0.20% + 수수료 0.03% + 슬리피지 약 0.27%)
- 대상: 캐시된 상장 종목 일봉(backtest/.cache/stocks), 2017-01 ~
  주의: 상장폐지 종목이 없어 결과가 실제보다 좋게 나오는 생존 편향이 있다.

신호
- all:   유동성 조건만 통과한 모든 종목-일 (기준선)
- surge: 전일 거래량 20일 평균의 3배 이상 + 전일 양봉 + 전일 상승률 +3~+15%
- gap:   surge 조건 + 당일 시가 갭 +1~+5%

포트폴리오: 매일 신호 중 전일 거래량 배수가 가장 큰 1종목에 전액(복리).
"""
import glob
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "backtest", ".cache", "stocks")
START = "2017-01-01"
COST = 0.005
TARGETS = [(0.03, 0.02), (0.05, 0.03), (0.05, 0.05), (0.10, 0.05)]


def load_all():
    frames = []
    for path in glob.glob(os.path.join(CACHE, "*.csv")):
        code = os.path.basename(path)[:-4]
        df = pd.read_csv(path, parse_dates=["Date"])
        if len(df) < 60:
            continue
        df = df[(df["Open"] > 0) & (df["Volume"] > 0)].copy()
        df["code"] = code
        df["prev_close"] = df["Close"].shift(1)
        df["prev_open"] = df["Open"].shift(1)
        df["prev_vol"] = df["Volume"].shift(1)
        df["avg_vol20"] = df["Volume"].shift(2).rolling(20).mean()
        df["avg_value20"] = (df["Close"] * df["Volume"]).shift(1).rolling(20).mean()
        df["prev_ret"] = df["prev_close"] / df["Close"].shift(2) - 1
        frames.append(df)
    data = pd.concat(frames, ignore_index=True)
    data = data[data["Date"] >= START]
    # 유동성: 전일 종가 1,000원 이상, 20일 평균 거래대금 10억 원 이상
    data = data[(data["prev_close"] >= 1000) & (data["avg_value20"] >= 1e9)]
    data["vol_ratio"] = data["prev_vol"] / data["avg_vol20"]
    data["gap"] = data["Open"] / data["prev_close"] - 1
    return data.dropna(subset=["vol_ratio", "gap", "prev_ret"])


def trade_returns(df, target, stop):
    up = df["High"] >= df["Open"] * (1 + target)
    down = df["Low"] <= df["Open"] * (1 - stop)
    close_ret = df["Close"] / df["Open"] - 1
    base = np.where(up, target, np.where(down, -stop, close_ret))
    conservative = np.where(up & down, -stop, base) - COST
    optimistic = np.where(up & down, target, base) - COST
    return conservative, optimistic, (up & down).mean()


def portfolio(df, rets):
    df = df.assign(r=rets)
    pick = df.sort_values("vol_ratio", ascending=False).groupby("Date").head(1)
    daily = pick.set_index("Date")["r"].sort_index()
    equity = (1 + daily).cumprod()
    years = (daily.index[-1] - daily.index[0]).days / 365.25
    cagr = equity.iloc[-1] ** (1 / years) - 1 if years > 0 else np.nan
    mdd = (equity / equity.cummax() - 1).min()
    yearly = (1 + daily).groupby(daily.index.year).prod() - 1
    return len(daily), cagr, mdd, yearly


def main():
    data = load_all()
    signals = {
        "all": data,
        "surge": data[(data["vol_ratio"] >= 3) & (data["prev_close"] > data["prev_open"])
                      & data["prev_ret"].between(0.03, 0.15)],
    }
    signals["gap"] = signals["surge"][signals["surge"]["gap"].between(0.01, 0.05)]
    print(f"기간 {data['Date'].min():%Y-%m-%d} ~ {data['Date'].max():%Y-%m-%d}, "
          f"종목 {data['code'].nunique()}개, 왕복 비용 {COST*100:.1f}%\n")
    rows = []
    for name, df in signals.items():
        for target, stop in TARGETS:
            cons, opt, both = trade_returns(df, target, stop)
            row = {
                "신호": name, "익절/손절": f"+{target*100:.0f}/-{stop*100:.0f}",
                "거래수": len(df), "동시도달%": round(both * 100, 1),
                "승률%(보수)": round((cons > 0).mean() * 100, 1),
                "평균순익%(보수)": round(cons.mean() * 100, 3),
                "평균순익%(낙관)": round(opt.mean() * 100, 3),
            }
            if name != "all":
                days, cagr, mdd, yearly = portfolio(df, cons)
                _, cagr_o, mdd_o, _ = portfolio(df, opt)
                row.update({
                    "매매일": days,
                    "연수익%(보수)": round(cagr * 100, 1), "MDD%(보수)": round(mdd * 100, 1),
                    "연수익%(낙관)": round(cagr_o * 100, 1), "MDD%(낙관)": round(mdd_o * 100, 1),
                })
                if (target, stop) == (0.05, 0.03):
                    row["연도별(보수)"] = " ".join(
                        f"{y}:{v*100:+.0f}%" for y, v in yearly.items()
                    )
            rows.append(row)
    out = pd.DataFrame(rows)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    pd.set_option("display.max_colwidth", 200)
    print(out.drop(columns=["연도별(보수)"], errors="ignore").to_string(index=False))
    print()
    for row in rows:
        if row.get("연도별(보수)"):
            print(f"[{row['신호']} +5/-3 연도별, 보수] {row['연도별(보수)']}")


if __name__ == "__main__":
    sys.exit(main())

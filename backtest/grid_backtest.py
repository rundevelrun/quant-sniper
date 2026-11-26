"""ETF 그리드 매매 백테스트(일봉 종가 근사).

자금을 N칸으로 나눠, 기준가에서 step만큼 내려갈 때마다 한 칸 매수하고, 각 칸은 매수가 대비
+step에 매도한다. 모든 칸이 현금일 때 가격이 오르면 기준가를 따라 올린다(아래로 벗어나면 전액 보유로 머문다).
비용: 편도 0.065%(수수료+슬리피지). 국내주식형(069500)은 매매차익 비과세, 나머지는 차익 15.4%.
비교: 같은 기간 그냥 보유(Buy & Hold). 분배금 미반영.
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backtest"))
from etf_rotation_backtest import load_prices  # noqa: E402

CACHE = os.path.join(ROOT, "backtest", ".cache")
ONE_WAY = 0.00065
TAX = 0.154
ASSETS = {"069500": "KODEX 200", "114260": "KODEX 국고채3년", "132030": "KODEX 골드선물(H)",
          "261240": "KODEX 미국달러선물"}


def grid(close, step, n, taxed):
    unit = 1.0 / n
    cash, base = 1.0, close.iloc[0]
    slots = [None] * n  # 각 칸의 (매수가, 수량)
    curve = []
    for px in close.values:
        # 모두 현금일 때 가격이 오르면 기준가를 따라 올려 그리드를 다시 깐다.
        if all(s is None for s in slots) and px > base:
            base = px
        for k in range(n):
            level = base * (1 - step * (k + 1))
            if slots[k] is None and px <= level and cash >= unit * 0.999:
                spend = min(unit, cash)
                slots[k] = (px, spend * (1 - ONE_WAY) / px)
                cash -= spend
            elif slots[k] is not None and px >= slots[k][0] * (1 + step):
                buy_px, qty = slots[k]
                proceeds = qty * px * (1 - ONE_WAY)
                gain = proceeds - qty * buy_px
                if taxed and gain > 0:
                    proceeds -= gain * TAX
                cash += proceeds
                slots[k] = None
        curve.append(cash + sum(q * px for s in slots if s for q in [s[1]]))
    return pd.Series(curve, index=close.index)


def stat(eq):
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    return ((eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1) * 100, (eq / eq.cummax() - 1).min() * 100


def main():
    data = load_prices(list(ASSETS), "2014-01-01", CACHE)
    for code, name in ASSETS.items():
        close = data[code]["Close"]
        close = close[close.index >= "2017-01-02"]
        bh = close / close.iloc[0]
        b_cagr, b_mdd = stat(bh)
        print(f"{name}: 보유 연 {b_cagr:.1f}% 낙폭 {b_mdd:.1f}%")
        for step in (0.01, 0.02, 0.03, 0.05):
            for n in (5, 10):
                eq = grid(close, step, n, taxed=code != "069500")
                c, m = stat(eq)
                halves = [stat(eq[a:b] / eq[a:b].iloc[0])[0] for a, b in (("2017", "2021"), ("2022", "2026"))]
                bh_halves = [stat(bh[a:b] / bh[a:b].iloc[0])[0] for a, b in (("2017", "2021"), ("2022", "2026"))]
                win = all(g > b for g, b in zip(halves, bh_halves))
                print(f"   간격 {step*100:.0f}% {n}칸: 연 {c:.1f}% 낙폭 {m:.1f}% "
                      f"앞 {halves[0]:.1f}/{bh_halves[0]:.1f} 뒤 {halves[1]:.1f}/{bh_halves[1]:.1f}"
                      f"{'  ← 두 기간 모두 보유보다 우위' if win else ''}")


if __name__ == "__main__":
    main()

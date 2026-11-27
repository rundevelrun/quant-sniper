"""시장 수급 과거 데이터 수집(ISA 앱키, 조회 전용).

- program_{KOSPI,KOSDAQ}.csv : 프로그램 매매 일자별(ka90010, 연속조회)
- investor_{KOSPI,KOSDAQ}.csv: 투자자별 순매수(ka10051, 날짜별 1회 조회, 종합 행)
저장: backtest/.cache/flows/. 이미 받은 날짜는 건너뛴다(이어받기).
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kiwoom_research import ResearchClient  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "backtest", ".cache", "flows")
MARKETS = {"KOSPI": ("P00101", "0", "001"), "KOSDAQ": ("P10102", "1", "101")}


def num(v):
    try:
        return float(str(v).replace("+", "").replace(",", ""))
    except ValueError:
        return None


def program(client, name, code, today):
    rows = client.paged("/api/dostk/mrkcond", "ka90010",
                        {"date": today, "amt_qty_tp": "1", "mrkt_tp": code, "min_tic_tp": "1", "stex_tp": "1"},
                        "prm_trde_trnsn", max_pages=40)
    df = pd.DataFrame([{"date": r["cntr_tm"][:8], "arb_net": num(r.get("dfrt_trde_netprps")),
                        "nonarb_net": num(r.get("ndiffpro_trde_netprps")), "all_net": num(r.get("all_netprps"))}
                       for r in rows]).drop_duplicates("date").sort_values("date")
    df.to_csv(os.path.join(OUT, f"program_{name}.csv"), index=False)
    print(name, "program", len(df), df["date"].min(), flush=True)


def investor(client, name, mcode, total_code, dates):
    path = os.path.join(OUT, f"investor_{name}.csv")
    done = set(pd.read_csv(path, dtype={"date": str})["date"]) if os.path.exists(path) else set()
    rows = []
    for i, d in enumerate(dates):
        if d in done:
            continue
        data, _, _ = client.call("/api/dostk/sect", "ka10051",
                                 {"mrkt_tp": mcode, "amt_qty_tp": "0", "stex_tp": "1", "base_dt": d})
        for r in data.get("inds_netprps") or []:
            if r.get("inds_cd") == total_code:
                rows.append({"date": d, "foreign": num(r.get("frgnr_netprps")), "inst": num(r.get("orgn_netprps")),
                             "indiv": num(r.get("ind_netprps")), "pension": num(r.get("endw_netprps"))})
        if len(rows) >= 200 or i == len(dates) - 1:
            df = pd.DataFrame(rows)
            df.to_csv(path, mode="a", header=not os.path.exists(path), index=False)
            rows = []
            print(name, "investor", d, flush=True)


def main():
    os.makedirs(OUT, exist_ok=True)
    client = ResearchClient()
    k200 = pd.read_csv(os.path.join(ROOT, "backtest", ".cache", "069500.csv"), index_col=0, parse_dates=True)
    dates = [d.strftime("%Y%m%d") for d in k200.index if d >= pd.Timestamp("2015-01-01")]
    today = dates[-1]
    for name, (pcode, _, _) in MARKETS.items():
        program(client, name, pcode, today)
    for name, (_, mcode, total) in MARKETS.items():
        investor(client, name, mcode, total, dates)


if __name__ == "__main__":
    main()

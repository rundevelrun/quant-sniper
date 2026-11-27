"""매일 장 마감 후 연구 데이터 증분 갱신(ISA 앱키 조회 전용 + 공개 데이터).

1) 시장 투자자별 순매수(ka10051): 없는 날짜만 추가
2) 프로그램 매매 일자별(ka90010): 최근 1페이지를 받아 병합
3) 주요 ETF 1분봉(ka10080): 최근 몇 페이지를 받아 병합(키움 보관 1년 한계를 넘어 누적)
4) VIX(야후), 미국 금리(FRED): 전체 재수신(작음)
"""
import json
import os
import sys
import urllib.request

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect_flows as cf  # noqa: E402
import collect_minutes as cm  # noqa: E402
from kiwoom_research import ResearchClient  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
C = os.path.join(ROOT, "backtest", ".cache")


def merge_csv(path, new, key):
    if os.path.exists(path):
        old = pd.read_csv(path, dtype={key: str} if key == "date" else None,
                          parse_dates=[key] if key == "time" else None)
        new = pd.concat([old, new])
    new = new.drop_duplicates(key, keep="last").sort_values(key)
    new.to_csv(path, index=False)
    return len(new)


def refresh_symbols(today):
    """매월 첫 실행 때 1분봉 수집 대상을 넓힌다(추가만, 삭제 없음)."""
    path = os.path.join(C, "minutes", "symbols.txt")
    stamp = os.path.join(C, "minutes", "symbols.month")
    month = today[:6]
    if os.path.exists(stamp) and open(stamp).read().strip() == month:
        return
    current = [x.strip() for x in open(path)] if os.path.exists(path) else []
    add = []
    try:
        sys.path.insert(0, ROOT)
        from config import CORE_TARGETS, ETF_ROTATION_ASSETS, PARKING_ETF_CODE
        add += list(CORE_TARGETS) + list(ETF_ROTATION_ASSETS) + [PARKING_ETF_CODE]
    except Exception as exc:
        print("설정 읽기 실패", exc, flush=True)
    listing = os.path.join(C, "stocks_all", "listing.csv")
    if os.path.exists(listing):
        lst = pd.read_csv(listing, dtype=str)
        add += lst[lst["market"] == "KOSPI"]["code"].tolist()[:70] + lst[lst["market"] == "KOSDAQ"]["code"].tolist()[:30]
    try:
        req = urllib.request.Request("https://finance.naver.com/api/sise/etfItemList.nhn",
                                     headers={"User-Agent": "Mozilla/5.0"})
        etfs = json.loads(urllib.request.urlopen(req, timeout=30).read().decode("euc-kr", "ignore"))
        items = sorted(etfs["result"]["etfItemList"], key=lambda x: -x["marketSum"])
        add += [x["itemcode"] for x in items[:50]]
    except Exception as exc:
        print("ETF 목록 실패", exc, flush=True)
    merged = list(dict.fromkeys([x for x in current if x] + add))
    open(path, "w").write("\n".join(merged) + "\n")
    open(stamp, "w").write(month)
    print("분봉 대상", len(current), "->", len(merged), flush=True)


def main():
    client = ResearchClient()
    today = pd.Timestamp.today().strftime("%Y%m%d")
    k = pd.read_csv(os.path.join(C, "069500.csv"), index_col=0, parse_dates=True)
    recent = [d.strftime("%Y%m%d") for d in k.index[-10:]]
    if today not in recent:
        recent.append(today)
    for name, (pcode, mcode, total) in cf.MARKETS.items():
        # 최근 날짜는 장중에 받은 중간값일 수 있으므로 지우고 다시 받는다.
        ipath = os.path.join(C, "flows", f"investor_{name}.csv")
        if os.path.exists(ipath):
            inv = pd.read_csv(ipath, dtype={"date": str})
            inv[~inv["date"].isin(recent)].to_csv(ipath, index=False)
        cf.investor(client, name, mcode, total, recent)
        rows = client.paged("/api/dostk/mrkcond", "ka90010",
                            {"date": today, "amt_qty_tp": "1", "mrkt_tp": pcode, "min_tic_tp": "1", "stex_tp": "1"},
                            "prm_trde_trnsn", max_pages=1)
        new = pd.DataFrame([{"date": r["cntr_tm"][:8], "arb_net": cf.num(r.get("dfrt_trde_netprps")),
                             "nonarb_net": cf.num(r.get("ndiffpro_trde_netprps")),
                             "all_net": cf.num(r.get("all_netprps"))} for r in rows])
        n = merge_csv(os.path.join(C, "flows", f"program_{name}.csv"), new, "date")
        print(name, "program rows", n, flush=True)
    refresh_symbols(today)
    for code in cm.symbols():
        try:
            rows = client.paged("/api/dostk/chart", "ka10080",
                                {"stk_cd": code, "tic_scope": "1", "upd_stkpc_tp": "1", "base_dt": today},
                                "stk_min_pole_chart_qry",
                                # 새로 추가된 종목은 키움이 보관한 1년 치를 처음에 한 번 받는다.
                                max_pages=8 if os.path.exists(os.path.join(C, "minutes", f"{code}.csv")) else 400)
        except Exception as exc:
            print(code, "분봉 실패", exc, flush=True)
            continue
        if not rows:
            continue
        new = pd.DataFrame({"time": pd.to_datetime([r["cntr_tm"] for r in rows], format="%Y%m%d%H%M%S"),
                            "open": [cm.num(r["open_pric"]) for r in rows], "high": [cm.num(r["high_pric"]) for r in rows],
                            "low": [cm.num(r["low_pric"]) for r in rows], "close": [cm.num(r["cur_prc"]) for r in rows],
                            "volume": [cm.num(r["trde_qty"]) for r in rows]})
        n = merge_csv(os.path.join(C, "minutes", f"{code}.csv"), new, "time")
        print(code, "minute rows", n, flush=True)
    for sid in ("DGS10", "DGS2", "T10Y2Y", "DTWEXBGS"):
        try:
            data = urllib.request.urlopen(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", timeout=30).read()
            open(os.path.join(C, "macro", f"{sid}.csv"), "wb").write(data)
        except Exception as exc:
            print("FRED 실패", sid, exc, flush=True)
    try:
        req = urllib.request.Request(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?period1=946684800&period2=1900000000&interval=1d",
            headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=30).read()
        json.loads(data)  # 형식 확인 후 저장
        open(os.path.join(C, "macro", "VIX.json"), "wb").write(data)
    except Exception as exc:
        print("VIX 실패", exc, flush=True)
    print("done", today, flush=True)


if __name__ == "__main__":
    main()

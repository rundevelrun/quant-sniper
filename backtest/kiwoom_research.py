"""연구용 키움 조회 클라이언트(ISA 앱키 사용 — 매매 봇 토큰과 분리, 주문 기능 없음).

키는 ~/.config/quant-sniper/isa.env 에서 읽는다. 연속조회(cont-yn/next-key) 지원.
"""
import json
import os
import time
import urllib.error
import urllib.request

BASE = "https://api.kiwoom.com"
ENV = os.path.expanduser("~/.config/quant-sniper/isa.env")


class ResearchClient:
    def __init__(self):
        env = dict(l.strip().split("=", 1) for l in open(ENV) if "=" in l)
        self.key, self.secret = env["KIWOOM_APP_KEY"], env["KIWOOM_APP_SECRET"]
        self.token, self.exp = None, 0
        self.last = 0.0

    def _token(self):
        if self.token and time.time() < self.exp - 60:
            return self.token
        req = urllib.request.Request(f"{BASE}/oauth2/token", method="POST",
                                     data=json.dumps({"grant_type": "client_credentials", "appkey": self.key,
                                                      "secretkey": self.secret}).encode(),
                                     headers={"Content-Type": "application/json;charset=UTF-8"})
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        self.token, self.exp = data["token"], time.time() + int(data.get("expires_in", 86400))
        return self.token

    def call(self, path, api_id, body, cont_yn="N", next_key=""):
        for attempt in range(4):
            wait = 0.25 - (time.time() - self.last)  # 초당 4회 이내
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            headers = {"Content-Type": "application/json;charset=UTF-8", "authorization": f"Bearer {self._token()}",
                       "api-id": api_id, "cont-yn": cont_yn, "next-key": next_key}
            req = urllib.request.Request(f"{BASE}{path}", data=json.dumps(body).encode(), headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    return json.loads(r.read()), r.headers.get("cont-yn", "N"), r.headers.get("next-key", "")
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503) and attempt < 3:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise
            except (urllib.error.URLError, TimeoutError):  # 네트워크 일시 장애
                if attempt < 3:
                    time.sleep(3 * (attempt + 1))
                    continue
                raise
        raise RuntimeError("retry exhausted")

    def paged(self, path, api_id, body, key, max_pages=50):
        rows, cont, nk = [], "N", ""
        for _ in range(max_pages):
            data, cont, nk = self.call(path, api_id, body, cont, nk)
            rows += data.get(key) or []
            if cont != "Y":
                break
        return rows

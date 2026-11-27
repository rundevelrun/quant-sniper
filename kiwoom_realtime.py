"""Kiwoom 0B realtime trade feed used as the live execution-price source."""

import asyncio
import datetime
import json
import threading
import time

from websockets import connect


class KiwoomRealtimeFeed:
    def __init__(self, token_provider, token_invalidator=None):
        self.token_provider = token_provider
        self.token_invalidator = token_invalidator
        self._lock = threading.Lock()
        self._symbols = set()
        self._quotes = {}
        self._high_water = {}
        self._connected = False
        self._last_error = "not started"
        self._generation = 0
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()

    def set_symbols(self, symbols):
        normalized = {str(code)[-6:] for code in symbols if code}
        with self._lock:
            if normalized != self._symbols:
                self._symbols = normalized
                self._generation += 1

    def quote(self, code, max_age_seconds=15):
        code = str(code)[-6:]
        with self._lock:
            quote = dict(self._quotes.get(code, {}))
        if not quote or time.time() - quote.get("received_at", 0) > max_age_seconds:
            return None
        return quote

    def reset_high_water(self, code, price):
        """신규 진입 이전의 당일 고가가 포지션 고점에 섞이지 않게 초기화한다."""
        with self._lock:
            self._high_water[str(code)[-6:]] = float(price)

    def is_healthy(self):
        with self._lock:
            return self._connected, self._last_error

    def _thread_main(self):
        asyncio.run(self._run_forever())

    async def _run_forever(self):
        while True:
            try:
                await self._connect_and_stream()
            except Exception as exc:
                with self._lock:
                    self._connected = False
                    self._last_error = f"{type(exc).__name__}: {exc}"
                await asyncio.sleep(3)

    async def _connect_and_stream(self):
        token = self.token_provider()
        if not token:
            raise RuntimeError("token unavailable")
        async with connect(
            "wss://api.kiwoom.com:10000/api/dostk/websocket",
            open_timeout=10, ping_interval=None,
        ) as websocket:
            await websocket.send(json.dumps({"trnm": "LOGIN", "token": token}))
            login = json.loads(await asyncio.wait_for(websocket.recv(), timeout=10))
            if int(login.get("return_code", -1)) != 0:
                message = str(login.get("return_msg", "websocket login failed"))
                if self.token_invalidator and ("8005" in message or "Token" in message):
                    self.token_invalidator(token)  # 다음 재접속에서 새 토큰으로 로그인
                raise RuntimeError(message)
            with self._lock:
                self._connected = True
                self._last_error = ""
                generation = -1

            while True:
                with self._lock:
                    symbols = sorted(self._symbols)
                    current_generation = self._generation
                if current_generation != generation and symbols:
                    await websocket.send(json.dumps({
                        "trnm": "REG", "grp_no": "1", "refresh": "1",
                        "data": [{"item": symbols, "type": ["0B"]}],
                    }))
                    generation = current_generation
                try:
                    raw = await asyncio.wait_for(websocket.recv(), timeout=2)
                except TimeoutError:
                    continue
                message = json.loads(raw)
                if str(message.get("trnm", "")).upper() == "PING":
                    await websocket.send(raw)
                    continue
                if message.get("return_code") not in (None, 0):
                    raise RuntimeError(message.get("return_msg", "realtime registration failed"))
                if message.get("trnm") != "REAL":
                    continue
                for record in message.get("data", []):
                    if record.get("type") != "0B":
                        continue
                    values = record.get("values", {})
                    code = str(record.get("item", ""))[-6:]
                    price = abs(float(values.get("10", 0) or 0))
                    if not code or price <= 0:
                        continue
                    quote = {
                        "price": price,
                        "open": abs(float(values.get("16", 0) or 0)),
                        "high": abs(float(values.get("17", 0) or 0)),
                        "low": abs(float(values.get("18", 0) or 0)),
                        "volume": abs(float(values.get("13", 0) or 0)),
                        "market_type": str(values.get("290", "")),
                        "trade_time": str(values.get("20", "")),
                        "trade_date": datetime.date.today().isoformat(),
                        "received_at": time.time(),
                    }
                    with self._lock:
                        self._high_water[code] = max(
                            self._high_water.get(code, price), price
                        )
                        quote["high_water"] = self._high_water[code]
                        self._quotes[code] = quote

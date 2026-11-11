"""Binance de mentira para os testes: responde às consultas públicas com os candles de exemplo do repositório.

Serve `/api/v3/time`, `/api/v3/klines`, `/api/v3/ticker/bookTicker`, `/api/v3/depth` e
`/fapi/v1/premiumIndex` como se o "agora" fosse o instante do relógio falso. Nada sai para a rede.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pandas as pd

from cryptoagent.data.models import KLINE_COLUMNS

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "exemplos" / "historico"


class FakeClock:
    """Relógio parado no instante escolhido (ms UTC)."""

    def __init__(self, now_ms: int) -> None:
        self.t = now_ms

    def __call__(self) -> int:
        return self.t


class FakeBinance:
    """Servidor falso. `latency_s` simula a demora de rede de cada consulta."""

    def __init__(self, clock: FakeClock, symbol: str = "ETHBRL", latency_s: float = 0.0, account: dict | None = None) -> None:
        self.clock = clock
        self.symbol = symbol
        self.latency_s = latency_s
        # conta simulada (endpoints assinados, só leitura): {"balances": [...], "trades": [...]}
        self.account = account
        self.calls: Counter[str] = Counter()
        self.frames = {p.stem: pd.read_parquet(p) for p in (SAMPLE / symbol).glob("*.parquet")}

    def transport(self) -> httpx.MockTransport:
        """Transporte para entregar ao cliente REST."""
        return httpx.MockTransport(self._handle)

    def last_price(self) -> float:
        """Fechamento do último candle de 1m aberto até agora."""
        one = self.frames["1m"]
        return float(one.loc[one["open_time"] <= self.clock(), "close"].iloc[-1])

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        if self.latency_s:
            await asyncio.sleep(self.latency_s)
        url = urlsplit(str(request.url))
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        self.calls[url.path] += 1
        now = self.clock()
        if url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": now})
        if url.path == "/api/v3/klines":
            df = self.frames[q["interval"]]
            df = df[df["open_time"] <= now]  # inclui o candle ainda aberto, como a Binance
            if "startTime" in q:
                df = df[df["open_time"] >= int(q["startTime"])].head(int(q.get("limit", 500)))
            else:
                df = df.tail(int(q.get("limit", 500)))
            rows = [[int(r[0]), *(f"{x:.8f}" for x in r[1:6]), int(r[6]), f"{r[7]:.8f}", int(r[8]), f"{r[9]:.8f}", f"{r[10]:.8f}", "0"] for r in df[KLINE_COLUMNS].itertuples(index=False)]
            return httpx.Response(200, json=rows)
        if url.path == "/sapi/v1/account/apiRestrictions" and self.account is not None:
            return httpx.Response(200, json={"ipRestrict": True, "enableReading": True, "enableWithdrawals": False, "enableSpotAndMarginTrading": False})
        if url.path == "/api/v3/account" and self.account is not None:
            return httpx.Response(200, json={"balances": self.account.get("balances", [])})
        if url.path == "/api/v3/account/commission" and self.account is not None:
            return httpx.Response(200, json={"standardCommission": {"maker": "0.001", "taker": "0.001"}, "discount": {"enabledAccount": False}})
        if url.path == "/api/v3/myTrades" and self.account is not None:
            a, b = int(q.get("startTime", 0)), int(q.get("endTime", 10**15))
            return httpx.Response(200, json=[t for t in self.account.get("trades", []) if a <= t["time"] <= b])
        price = self.last_price()
        if url.path == "/api/v3/ticker/bookTicker":
            return httpx.Response(200, json={"symbol": q["symbol"], "bidPrice": f"{price * 0.9999:.2f}", "bidQty": "1.5", "askPrice": f"{price * 1.0001:.2f}", "askQty": "1.2"})
        if url.path == "/api/v3/depth":
            bids = [[f"{price * (0.9999 - i * 0.0001):.2f}", "1.00000000"] for i in range(20)]
            asks = [[f"{price * (1.0001 + i * 0.0001):.2f}", "0.80000000"] for i in range(20)]
            return httpx.Response(200, json={"lastUpdateId": 1, "bids": bids, "asks": asks})
        if url.path == "/fapi/v1/premiumIndex":
            return httpx.Response(200, json={"symbol": q["symbol"], "lastFundingRate": "0.00010000", "nextFundingTime": now + 3_600_000})
        return httpx.Response(404, json={"code": -1, "msg": "rota não simulada"})

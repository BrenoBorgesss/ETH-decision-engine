"""Replay: o paper trading rodando sobre o histórico gravado, com o relógio acelerado.

O laço do paper (`paper/runner.py`) e o motor (`engine/live.py`) são EXATAMENTE os do modo ao vivo.
Só o cliente da Binance é trocado por este, que responde com os candles do histórico local num
relógio simulado: cada "espera" do laço apenas adianta o relógio. Nada sai para a rede (o transporte
HTTP recusa qualquer requisição) e nenhum endpoint de conta é atendido.

Como cada dado é reconstruído, e o que muda em relação ao ao vivo:
- Candles: só os já FECHADOS no instante simulado (a Binance também devolve o candle em formação; aqui
  ele não existe, porque o histórico só tem candles completos).
- Preço agora: abertura do candle de 1m em curso; bid/ask = esse preço ± meio spread da tabela de custos
  daquela hora (a mesma do backtest).
- Order book: simétrico (desequilíbrio zero); não existe histórico do livro.
- Derivativos (funding): indisponíveis; viram aviso não crítico, como quando a API de futuros cai.
- Perfil por horário: desligado (o perfil salvo foi calculado com dados posteriores; usá-lo seria olhar o futuro).
- Regras de tamanho de ordem: do arquivo salvo (`paper.filtros_arquivo`).
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import numpy as np
import pandas as pd

from cryptoagent.backtest.costs import CostSchedule
from cryptoagent.config import Config
from cryptoagent.data.history import HistoryStore
from cryptoagent.data.rest import (
    ALLOWED_PATHS,
    SIGNED_READ,
    ApiResponse,
    BinanceError,
    BinanceReadOnlyClient,
    ForbiddenEndpoint,
    MissingCredentials,
)
from cryptoagent.paper.filters import load_saved
from cryptoagent.timeutils import interval_ms, ms_to_local

MIN_MS = 60_000
WARMUP_DAYS = 35  # EMA 200 no 4h precisa de ~33 dias de candles antes do início


class _Clock:
    def __init__(self, t: int) -> None:
        self.t = t

    def __call__(self) -> int:
        return self.t


def _no_network(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"o replay não pode acessar a rede (tentou {request.url.path})")


@dataclass
class _Series:
    frame: pd.DataFrame
    open_time: np.ndarray
    close_time: np.ndarray


class ReplayClient(BinanceReadOnlyClient):
    """Cliente "Binance" que responde com o histórico local, num relógio simulado (ver a descrição do módulo)."""

    def __init__(self, cfg: Config, *, start_ms: int, costs: CostSchedule) -> None:
        self.clock = _Clock(start_ms)
        super().__init__(
            cfg.binance, None, transport=httpx.MockTransport(_no_network), sleep=self.advance, clock=self.clock
        )
        self._cfg_all = cfg
        self._store = HistoryStore(cfg.path(cfg.historico.pasta))
        self._series: dict[tuple[str, str], _Series] = {}
        self._costs = costs
        self._tz = cfg.fuso.local
        self.requests: dict[str, int] = {}

    async def advance(self, seconds: float) -> None:
        """A "espera" do replay: só adianta o relógio simulado."""
        self.clock.t += round(seconds * 1000)

    def _get_series(self, symbol: str, interval: str) -> _Series:
        key = (symbol, interval)
        if key not in self._series:
            df = self._store.load(symbol, interval)
            if df.empty:
                raise BinanceError(f"sem histórico de {symbol} {interval} para o replay", path="/api/v3/klines")
            self._series[key] = _Series(df, df["open_time"].to_numpy(), df["close_time"].to_numpy())
        return self._series[key]

    def _price_now(self, symbol: str, path: str) -> float:
        s = self._get_series(symbol, "1m")
        now = self.clock()
        i = int(np.searchsorted(s.open_time, now, side="right")) - 1
        if i < 0 or now >= int(s.open_time[i]) + MIN_MS:
            raise BinanceError(f"o histórico de {symbol} não cobre este instante do replay", path=path)
        return float(s.frame["open"].iloc[i])

    async def get(self, path: str, params: dict | None = None) -> ApiResponse:
        """Responde como a Binance, a partir do histórico, para os caminhos de leitura pública usados pelo paper."""
        if path not in ALLOWED_PATHS:
            raise ForbiddenEndpoint("Endpoint fora da lista somente leitura; requisição bloqueada", path=path)
        if path in SIGNED_READ:
            raise MissingCredentials("O replay nunca lê a conta", path=path)
        self.requests[path] = self.requests.get(path, 0) + 1
        params = params or {}
        now = self.clock()
        if path == "/api/v3/time":
            data: object = {"serverTime": now}
        elif path == "/api/v3/ticker/bookTicker":
            sym = params["symbol"]
            price = self._price_now(sym, path)
            half = self._costs.half_spread(ms_to_local(now, self._tz).hour) if sym == self._costs.symbol else 0.00005
            data = {
                "symbol": sym,
                "bidPrice": f"{price * (1 - half):.8f}",
                "askPrice": f"{price * (1 + half):.8f}",
                "bidQty": "1",
                "askQty": "1",
            }
        elif path == "/api/v3/depth":
            price = self._price_now(params["symbol"], path)
            levels = range(int(params.get("limit", 20)))
            data = {
                "bids": [[f"{price * (1 - 0.0001 * (i + 1)):.8f}", "1"] for i in levels],
                "asks": [[f"{price * (1 + 0.0001 * (i + 1)):.8f}", "1"] for i in levels],
            }
        elif path == "/api/v3/exchangeInfo":
            sym = params.get("symbol", self._cfg_all.paper.par)
            f = load_saved(self._cfg_all.path(self._cfg_all.paper.filtros_arquivo), sym)
            if f is None:
                raise BinanceError(f"sem regras de ordem salvas para {sym}", path=path)
            filters = [
                {"filterType": "LOT_SIZE", "minQty": str(f.min_qty), "maxQty": "1e9", "stepSize": str(f.step)},
                {"filterType": "NOTIONAL", "minNotional": str(f.min_notional)},
                {"filterType": "PRICE_FILTER", "tickSize": str(f.tick)},
            ]
            if f.max_market_qty:
                filters.append({"filterType": "MARKET_LOT_SIZE", "maxQty": str(f.max_market_qty)})
            data = {"symbols": [{"symbol": sym, "filters": filters}]}
        else:
            raise BinanceError("dado sem histórico: indisponível no replay", path=path)
        return ApiResponse(data, path, now, 0.0, 0)

    async def klines(
        self, symbol: str, interval: str, *, start_ms: int | None = None, end_ms: int | None = None, limit: int = 500
    ) -> tuple[pd.DataFrame, ApiResponse]:
        """Candles já FECHADOS no instante simulado (os mais recentes, ou a partir de `start_ms`)."""
        self.requests["/api/v3/klines"] = self.requests.get("/api/v3/klines", 0) + 1
        s = self._get_series(symbol, interval)
        now = self.clock()
        closed = int(np.searchsorted(s.close_time, now, side="left"))  # candles com close_time < agora
        if start_ms is not None:
            a = int(np.searchsorted(s.open_time, start_ms, side="left"))
            b = min(a + limit, closed)
        else:
            b = closed
            a = max(0, b - limit)
        if end_ms is not None:
            b = min(b, int(np.searchsorted(s.open_time, end_ms, side="right")))
        frame = s.frame.iloc[a : max(a, b)].reset_index(drop=True)
        return frame, ApiResponse(None, "/api/v3/klines", now, 0.0, 0)

    async def aclose(self) -> None:
        """Nada a fechar além do cliente HTTP (que nunca foi usado)."""
        await super().aclose()


def replay_window(store: HistoryStore, symbol: str, days: int, tz: str, start_day: str | None) -> tuple[int, int]:
    """(início, fim) do replay em ms: `days` dias locais a partir de `start_day` (AAAA-MM-DD) ou, sem ele, os últimos dias completos do histórico."""
    from datetime import date, timedelta

    from cryptoagent.timeutils import local_trading_day, trading_day_start_ms

    df = store.load(symbol, "1m")
    if df.empty:
        raise ValueError(f"sem histórico de {symbol} 1m para o replay (rode: cryptoagent historico baixar)")
    first, last = int(df["open_time"].iloc[0]), int(df["close_time"].iloc[-1])
    if start_day:
        day = date.fromisoformat(start_day)
    else:
        day = local_trading_day(last - interval_ms("1m"), tz) - timedelta(days=days)
    start = trading_day_start_ms(day, tz)
    end = trading_day_start_ms(day + timedelta(days=days), tz)
    if start < first + WARMUP_DAYS * 86_400_000:
        raise ValueError(
            f"o replay precisa de pelo menos {WARMUP_DAYS} dias de histórico antes do início (aquecimento dos indicadores, ex.: EMA 200 do 4h)"
        )
    if end > last + 1:
        raise ValueError(
            f"o histórico de {symbol} acaba antes do fim do replay ({ms_to_local(last, tz):%d/%m/%Y %H:%M})"
        )
    return start, end

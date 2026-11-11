"""Coletor de custos ao vivo: spread, profundidade e slippage simulado.

Pares: os candidatos (ETHUSDT, ETHBRL) e o câmbio (USDTBRL).
O order book não tem histórico na Binance, então esta coleta precisa ficar rodando
por dias (mínimo 7, ideal 14), cobrindo dias úteis e fim de semana.

Fontes: WebSocket bookTicker (melhor compra/venda em tempo real) e depth20 (20 níveis,
atualizado a cada 1 s). Se o WebSocket ficar mudo, cai para consulta REST (/api/v3/depth).

Observação: essas mensagens da Binance não trazem horário da corretora; o timestamp
gravado é o de chegada no PC (UTC), e o relógio do PC é conferido com o da Binance.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import dataclass, field

from rich.console import Console

from cryptoagent.config import Config
from cryptoagent.costs.orderbook import Level, depth_quote, simulate_market_buy, simulate_market_sell, spread_pct
from cryptoagent.data.rest import BinanceError, BinanceReadOnlyClient
from cryptoagent.data.websocket import BinanceStream
from cryptoagent.security.redact import redact
from cryptoagent.storage import db
from cryptoagent.timeutils import format_local, now_ms

log = logging.getLogger(__name__)


@dataclass
class BookState:
    """Último estado conhecido do livro de um par: melhor compra e venda, níveis de profundidade e quando cada um chegou."""
    symbol: str
    bid: float | None = None
    ask: float | None = None
    bid_qty: float | None = None
    ask_qty: float | None = None
    ticker_ms: int | None = None
    bids: list[Level] = field(default_factory=list)
    asks: list[Level] = field(default_factory=list)
    depth_ms: int | None = None
    depth_source: str = "ws"

    def mid(self) -> float | None:
        """Preço médio entre a melhor compra e a melhor venda, ou None sem dados."""
        if self.bid and self.ask:
            return (self.bid + self.ask) / 2
        if self.bids and self.asks:
            return (self.bids[0][0] + self.asks[0][0]) / 2
        return None


def _levels(raw: list) -> list[Level]:
    return [(float(p), float(q)) for p, q in raw]


def quote_sizes(symbol: str, sizes_brl: list[float], fx_usdtbrl: float | None) -> dict[str, float | None]:
    """Converte os tamanhos de ordem (em R$) para a moeda de cotação do par."""
    out: dict[str, float | None] = {}
    for brl in sizes_brl:
        key = f"{brl:g}"
        if symbol.endswith("BRL"):
            out[key] = brl
        elif symbol.endswith("USDT"):
            out[key] = brl / fx_usdtbrl if fx_usdtbrl else None
        else:
            out[key] = None
    return out


def build_sample(book: BookState, now: int, sizes: dict[str, float | None], fx_usdtbrl: float | None, ticker_max_age_ms: int) -> dict:
    """Uma linha de custos para um par, a partir do estado atual do livro."""
    ticker_fresh = book.ticker_ms is not None and now - book.ticker_ms <= ticker_max_age_ms and book.bid and book.ask
    if ticker_fresh:
        bid, ask, bid_qty, ask_qty = book.bid, book.ask, book.bid_qty, book.ask_qty
        source = f"{book.depth_source}+bookTicker"
    else:
        (bid, bid_qty), (ask, ask_qty) = book.bids[0], book.asks[0]
        source = book.depth_source

    slippage: dict[str, dict | None] = {}
    for key, quote in sizes.items():
        if quote is None:
            slippage[key] = None
            continue
        buy = simulate_market_buy(book.asks, quote)
        sell = simulate_market_sell(book.bids, quote / book.bids[0][0])
        slippage[key] = {
            "compra_pct": round(buy.slippage_pct or 0.0, 6),
            "venda_pct": round(sell.slippage_pct or 0.0, 6),
            "completo": buy.complete and sell.complete,
        }

    return {
        "ts_ms": now,
        "symbol": book.symbol,
        "source": source,
        "bid": bid,
        "ask": ask,
        "bid_qty": bid_qty,
        "ask_qty": ask_qty,
        "spread_pct": spread_pct(bid, ask),
        "depth_bid_quote_5": depth_quote(book.bids, 5),
        "depth_ask_quote_5": depth_quote(book.asks, 5),
        "depth_bid_quote_20": depth_quote(book.bids, 20),
        "depth_ask_quote_20": depth_quote(book.asks, 20),
        "fx_usdtbrl": fx_usdtbrl,
        "slippage_json": slippage,
        "book_age_ms": now - book.depth_ms if book.depth_ms is not None else None,
        "ticker_age_ms": now - book.ticker_ms if book.ticker_ms is not None else None,
    }


class CostRecorder:
    """Coletor de custos: recebe o livro pelo WebSocket (com a API REST como reserva) e grava amostras de spread, profundidade e slippage no SQLite."""
    def __init__(self, cfg: Config, client: BinanceReadOnlyClient, conn: sqlite3.Connection, *, console: Console | None = None) -> None:
        self.cfg = cfg
        self.client = client
        self.conn = conn
        self.console = console or Console()
        self.symbols = cfg.mercado.simbolos_monitorados
        self.books = {s: BookState(s) for s in self.symbols}
        streams = [f"{s.lower()}@bookTicker" for s in self.symbols] + [f"{s.lower()}@depth20@1000ms" for s in self.symbols]
        self.stream = BinanceStream(cfg.binance.ws_url, streams, self.on_message, name="custos")
        self.samples_written = 0
        self.rest_fallbacks = 0
        self._last_rows: dict[str, dict] = {}

    def on_message(self, stream: str, data: dict, received_ms: int) -> None:
        """Recebe uma mensagem do WebSocket (bookTicker ou depth) e atualiza o estado do livro do par."""
        name, _, kind = stream.partition("@")
        book = self.books.get(name.upper())
        if book is None:
            return
        if kind == "bookTicker":
            book.bid, book.bid_qty = float(data["b"]), float(data["B"])
            book.ask, book.ask_qty = float(data["a"]), float(data["A"])
            book.ticker_ms = received_ms
        elif kind.startswith("depth"):
            book.bids, book.asks = _levels(data["bids"]), _levels(data["asks"])
            book.depth_ms = received_ms
            book.depth_source = "ws"

    async def _refresh_from_rest(self, book: BookState) -> None:
        resp = await self.client.get("/api/v3/depth", {"symbol": book.symbol, "limit": 20})
        book.bids, book.asks = _levels(resp.data["bids"]), _levels(resp.data["asks"])  # type: ignore[index]
        book.depth_ms = resp.received_ms
        book.depth_source = "rest"
        self.rest_fallbacks += 1

    async def sample_once(self, now: int | None = None) -> list[dict]:
        """Grava uma amostra de cada par com livro fresco; livro velho é buscado pela API REST antes. Devolve as linhas gravadas."""
        max_age_ms = int(self.cfg.saude_dados.order_book_max_s * 1000)
        for book in self.books.values():
            stale = book.depth_ms is None or (now or now_ms()) - book.depth_ms > max_age_ms
            if stale:
                try:
                    await self._refresh_from_rest(book)
                    db.log_event(self.conn, now_ms(), "rest_fallback", book.symbol)
                except BinanceError as exc:
                    log.warning("Falha no fallback REST de %s: %s", book.symbol, exc)
                    db.log_event(self.conn, now_ms(), "error", redact(exc))

        now = now or now_ms()
        fx_book = self.books.get(self.cfg.mercado.par_cambio)
        fx_fresh = fx_book is not None and fx_book.depth_ms is not None and now - fx_book.depth_ms <= max_age_ms
        fx_mid = fx_book.mid() if fx_fresh and fx_book else None

        rows = []
        for book in self.books.values():
            if not book.bids or not book.asks or book.depth_ms is None or now - book.depth_ms > max_age_ms:
                continue  # sem livro fresco: não grava dado velho
            try:
                sizes = quote_sizes(book.symbol, self.cfg.custos.tamanhos_ordem_brl, fx_mid)
                rows.append(build_sample(book, now, sizes, fx_mid, max_age_ms))
            except ValueError as exc:
                log.warning("Amostra inválida de %s descartada: %s", book.symbol, exc)
        self.samples_written += db.insert_samples(self.conn, rows)
        self._last_rows.update({r["symbol"]: r for r in rows})
        return rows

    def snapshot_once(self, now: int | None = None) -> None:
        """Grava o livro completo de cada par (níveis de compra e venda)."""
        now = now or now_ms()
        for book in self.books.values():
            if book.bids and book.asks:
                db.insert_snapshot(self.conn, now, book.symbol, book.depth_source, book.bids, book.asks)

    async def _sample_loop(self, stop: asyncio.Event) -> None:
        every = self.cfg.custos.amostra_a_cada_s
        snap_every = self.cfg.custos.snapshot_completo_a_cada_s
        last_snapshot = 0.0
        last_status = 0.0
        loop = asyncio.get_running_loop()
        await _wait(stop, 3)  # dá tempo de o WebSocket entregar o primeiro livro
        while not stop.is_set():
            started = loop.time()
            await self.sample_once()
            if started - last_snapshot >= snap_every:
                self.snapshot_once()
                last_snapshot = started
            if started - last_status >= 60:
                self.print_status()
                last_status = started
            await _wait(stop, max(0.0, every - (loop.time() - started)))

    def print_status(self) -> None:
        """Imprime uma linha de andamento: amostras gravadas, spread de cada par e estado do WebSocket."""
        tz = self.cfg.fuso.local
        parts = []
        for sym in self.symbols:
            row = self._last_rows.get(sym)
            parts.append(f"{sym} spread {row['spread_pct']:.4f}%" if row else f"{sym} sem dados")
        ws = "WS conectado" if self.stream.connected else "WS DESCONECTADO (usando REST)"
        self.console.print(
            f"[dim]{format_local(now_ms(), tz, '%d/%m %H:%M:%S')}[/] | amostras {self.samples_written} | "
            + " | ".join(parts)
            + f" | {ws} | reconexões {self.stream.reconnects} | fallbacks REST {self.rest_fallbacks}"
        )

    async def run(self, stop: asyncio.Event, duration_s: float | None = None) -> None:
        """Roda a coleta até `stop` ser acionado ou passarem `duration_s` segundos."""
        db.log_event(self.conn, now_ms(), "start", ",".join(self.symbols))
        tasks = [asyncio.create_task(self.stream.run(stop)), asyncio.create_task(self._sample_loop(stop))]
        if duration_s:
            tasks.append(asyncio.create_task(_stop_after(stop, duration_s)))
        try:
            await asyncio.gather(*tasks)
        finally:
            stop.set()
            for t in tasks:
                t.cancel()
            db.log_event(self.conn, now_ms(), "stop", f"amostras={self.samples_written}")


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def _stop_after(stop: asyncio.Event, seconds: float) -> None:
    await _wait(stop, seconds)
    stop.set()

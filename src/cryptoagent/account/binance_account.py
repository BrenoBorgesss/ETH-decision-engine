"""Leitura da conta (somente leitura): saldos, taxas efetivas e operações executadas.

As operações são guardadas no banco local e sincronizadas aos poucos. A Binance só
aceita janelas de até 24 h em /api/v3/myTrades, então a sincronização anda de janela
em janela a partir da última feita.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from cryptoagent.account.ledger import LedgerTrade
from cryptoagent.data.models import Stamped
from cryptoagent.data.rest import BinanceError, BinanceReadOnlyClient
from cryptoagent.storage.agent_db import get_meta, set_meta
from cryptoagent.timeutils import floor_to_interval

log = logging.getLogger(__name__)

KNOWN_QUOTES = ("USDT", "BRL", "FDUSD", "USDC", "BTC", "BNB", "ETH")
DAY_MS = 86_400_000


def split_symbol(symbol: str) -> tuple[str, str]:
    """ETHBRL -> ("ETH", "BRL"); ETHUSDT -> ("ETH", "USDT")."""
    for quote in KNOWN_QUOTES:
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)], quote
    raise ValueError(f"Não sei separar base/cotação de {symbol}")


# ------------------------------------------------------------------ saldos


@dataclass(frozen=True)
class Balance:
    """Saldo de um ativo na conta: livre e travado em ordens."""

    asset: str
    free: float
    locked: float

    @property
    def total(self) -> float:
        """Livre + travado."""
        return self.free + self.locked


async def fetch_balances(client: BinanceReadOnlyClient) -> Stamped[dict[str, Balance]]:
    """Lê os saldos da conta (/api/v3/account, somente leitura), sem os ativos zerados. Devolve {ativo: Balance} com fonte e horário."""
    resp = await client.get("/api/v3/account", {"omitZeroBalances": "true"})
    balances = {
        b["asset"]: Balance(b["asset"], float(b["free"]), float(b["locked"]))
        for b in resp.data.get("balances", [])  # type: ignore[union-attr]
    }
    return Stamped(balances, resp.source, resp.received_ms, latency_ms=resp.latency_ms)


# ------------------------------------------------------------------- taxas


@dataclass(frozen=True)
class CommissionInfo:
    """Taxas maker/taker de um par e a situação do desconto por pagar taxas em BNB."""

    symbol: str
    maker: float  # fração (0,001 = 0,1%), já somando taxa padrão + especial + imposto
    taker: float
    bnb_discount_enabled: bool  # "pagar taxas com BNB" ligado na conta E válido para o par
    bnb_discount_factor: float  # ex.: 0,75 = paga 75% da taxa quando usa BNB
    from_account: bool  # False = leitura falhou, valores vieram do config

    def effective(self, side: str, *, has_bnb: bool) -> float:
        """Taxa efetiva (fração) do lado pedido ("taker" ou "maker"), com o desconto de BNB quando ele está ligado e há BNB na conta."""
        rate = self.taker if side == "taker" else self.maker
        if self.bnb_discount_enabled and has_bnb:
            rate *= self.bnb_discount_factor
        return rate


def parse_commission(symbol: str, data: dict) -> CommissionInfo:
    """Converte a resposta de /api/v3/account/commission em CommissionInfo, somando taxa padrão, especial e imposto."""

    def total(kind: str) -> float:
        return sum(
            float(data.get(group, {}).get(kind, 0) or 0)
            for group in ("standardCommission", "specialCommission", "taxCommission")
        )

    discount = data.get("discount", {}) or {}
    enabled = (
        bool(discount.get("enabledAccount"))
        and bool(discount.get("enabledSymbol"))
        and discount.get("discountAsset") == "BNB"
    )
    factor = float(discount.get("discount", 1) or 1)
    return CommissionInfo(symbol, total("maker"), total("taker"), enabled, factor if 0 < factor <= 1 else 1.0, True)


async def fetch_commission(client: BinanceReadOnlyClient, symbol: str) -> Stamped[CommissionInfo]:
    """Lê as taxas da conta para o par (somente leitura)."""
    resp = await client.get("/api/v3/account/commission", {"symbol": symbol})
    return Stamped(parse_commission(symbol, resp.data), resp.source, resp.received_ms)  # type: ignore[arg-type]


def fallback_commission(symbol: str, maker: float, taker: float) -> CommissionInfo:
    """CommissionInfo com as taxas do config, sem desconto de BNB, para quando a leitura da conta falha."""
    return CommissionInfo(symbol, maker, taker, False, 1.0, False)


# --------------------------------------------------------------- operações

PriceAt = Callable[[str, int], Awaitable[float | None]]


@dataclass(frozen=True)
class Fill:
    """Uma execução (negócio) da conta, como a Binance devolve em /api/v3/myTrades."""

    symbol: str
    trade_id: int
    order_id: int
    time_ms: int
    is_buyer: bool
    price: float
    qty: float
    quote_qty: float
    commission: float
    commission_asset: str
    is_maker: bool


def parse_trade(symbol: str, d: dict) -> Fill:
    """Converte um item de /api/v3/myTrades em Fill."""
    return Fill(
        symbol=symbol,
        trade_id=int(d["id"]),
        order_id=int(d.get("orderId", 0)),
        time_ms=int(d["time"]),
        is_buyer=bool(d["isBuyer"]),
        price=float(d["price"]),
        qty=float(d["qty"]),
        quote_qty=float(d.get("quoteQty", float(d["price"]) * float(d["qty"]))),
        commission=float(d["commission"]),
        commission_asset=str(d["commissionAsset"]),
        is_maker=bool(d.get("isMaker", False)),
    )


async def fee_in_quote(fill: Fill, price_at: PriceAt, fallback_rate: float) -> tuple[float, bool]:
    """Converte a taxa para a moeda de cotação do par. Retorna (valor, estimada?)."""
    base, quote = split_symbol(fill.symbol)
    asset = fill.commission_asset
    if fill.commission == 0:
        return 0.0, False
    if asset == quote:
        return fill.commission, False
    if asset == base:
        return fill.commission * fill.price, False
    price = await price_at(f"{asset}{quote}", fill.time_ms)
    if price:
        return fill.commission * price, False
    log.warning("Não foi possível converter taxa em %s de %s; usando estimativa do config", asset, fill.symbol)
    return fill.quote_qty * fallback_rate, True


def make_price_at(client: BinanceReadOnlyClient) -> PriceAt:
    """Preço de um par num instante (fechamento do candle de 1m), com cache."""
    cache: dict[tuple[str, int], float | None] = {}

    async def price_at(symbol: str, time_ms: int) -> float | None:
        minute = floor_to_interval(time_ms, "1m")
        key = (symbol, minute)
        if key not in cache:
            try:
                df, _ = await client.klines(symbol, "1m", start_ms=minute, limit=1)
                cache[key] = float(df["close"].iloc[0]) if not df.empty else None
            except BinanceError:
                cache[key] = None
        return cache[key]

    return price_at


def _ledger_start_key(symbol: str) -> str:
    return f"ledger_start_ms:{symbol}"


def _synced_until_key(symbol: str) -> str:
    return f"trades_synced_until_ms:{symbol}"


def ledger_start_ms(conn: sqlite3.Connection, symbol: str) -> int | None:
    """Instante (ms) a partir do qual as operações do par são acompanhadas, ou None se ainda não foi definido."""
    value = get_meta(conn, _ledger_start_key(symbol))
    return int(value) if value else None


def ensure_ledger_start(conn: sqlite3.Connection, symbol: str, day_start_ms: int) -> int:
    """Na primeira vez, o livro da operação começa no início do dia de hoje.

    Tudo o que você já tinha antes disso fica FORA da operação (ignorado).
    """
    current = ledger_start_ms(conn, symbol)
    if current is None:
        set_meta(conn, _ledger_start_key(symbol), str(day_start_ms))
        return day_start_ms
    return current


def upsert_fill(conn: sqlite3.Connection, fill: Fill, fee_quote: float, estimated: bool) -> None:
    """Grava a execução em account_trades; se ela já existe, atualiza só a taxa convertida e a marca de taxa estimada."""
    conn.execute(
        """INSERT INTO account_trades (symbol, trade_id, order_id, time_ms, is_buyer, price, qty, quote_qty,
               commission, commission_asset, is_maker, fee_quote, fee_estimated)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(symbol, trade_id) DO UPDATE SET fee_quote = excluded.fee_quote, fee_estimated = excluded.fee_estimated""",
        (
            fill.symbol,
            fill.trade_id,
            fill.order_id,
            fill.time_ms,
            int(fill.is_buyer),
            fill.price,
            fill.qty,
            fill.quote_qty,
            fill.commission,
            fill.commission_asset,
            int(fill.is_maker),
            fee_quote,
            int(estimated),
        ),
    )


def load_account_trades(conn: sqlite3.Connection, symbol: str, since_ms: int) -> list[LedgerTrade]:
    """Operações sincronizadas, no formato do livro da operação."""
    base, _ = split_symbol(symbol)
    rows = conn.execute(
        "SELECT * FROM account_trades WHERE symbol = ? AND time_ms >= ? ORDER BY time_ms, trade_id", (symbol, since_ms)
    ).fetchall()
    return [
        LedgerTrade(
            trade_id=str(r["trade_id"]),
            time_ms=r["time_ms"],
            side="buy" if r["is_buyer"] else "sell",
            qty=r["qty"],
            price=r["price"],
            fee_quote=r["fee_quote"],
            fee_in_base=r["commission"] if (r["is_buyer"] and r["commission_asset"] == base) else 0.0,
            fee_estimated=bool(r["fee_estimated"]),
        )
        for r in rows
    ]


async def sync_trades(
    client: BinanceReadOnlyClient,
    conn: sqlite3.Connection,
    symbol: str,
    *,
    server_now_ms: int,
    start_ms: int,
    price_at: PriceAt,
    fallback_fee_rate: float,
    max_windows: int = 120,
) -> int:
    """Busca as operações novas desde a última sincronização. Retorna quantas foram gravadas."""
    synced = get_meta(conn, _synced_until_key(symbol))
    window_start = max(start_ms, int(synced) if synced else start_ms)
    written = 0
    for _ in range(max_windows):
        if window_start >= server_now_ms:
            break
        window_end = min(window_start + DAY_MS - 1, server_now_ms)
        resp = await client.get(
            "/api/v3/myTrades", {"symbol": symbol, "startTime": window_start, "endTime": window_end, "limit": 1000}
        )
        batch = [parse_trade(symbol, d) for d in resp.data]  # type: ignore[union-attr]
        if len(batch) == 1000:
            # Janela com mais de 1000 operações: continua pelo id até sair da janela.
            while True:
                more = await client.get(
                    "/api/v3/myTrades", {"symbol": symbol, "fromId": batch[-1].trade_id + 1, "limit": 1000}
                )
                page = [parse_trade(symbol, d) for d in more.data]  # type: ignore[union-attr]
                inside = [f for f in page if f.time_ms <= window_end]
                batch.extend(inside)
                if len(page) < 1000 or len(inside) < len(page):
                    break
        for fill in batch:
            fee, estimated = await fee_in_quote(fill, price_at, fallback_fee_rate)
            upsert_fill(conn, fill, fee, estimated)
            written += 1
        conn.commit()
        window_start = window_end + 1
    else:
        log.warning(
            "Sincronização de %s parou no limite de %d janelas; rode de novo para continuar", symbol, max_windows
        )
    # Margem de 2 min: operações recém-executadas às vezes aparecem com pequeno atraso.
    set_meta(conn, _synced_until_key(symbol), str(max(start_ms, min(window_start, server_now_ms) - 120_000)))
    return written

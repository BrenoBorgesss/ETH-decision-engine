"""Simulação de ordem a mercado consumindo o order book, para estimar o slippage.

Slippage = quanto o preço médio de execução ficou PIOR que o melhor preço do livro.
Positivo sempre significa custo (compra mais cara ou venda mais barata).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

Level = tuple[float, float]  # (preço, quantidade do ativo base)


@dataclass(frozen=True)
class FillEstimate:
    side: str  # "buy" | "sell"
    best_price: float
    avg_price: float | None
    filled_base: float
    filled_quote: float
    slippage_pct: float | None
    complete: bool  # False = os níveis disponíveis não tinham volume suficiente
    levels_used: int


def _check_book(levels: Sequence[Level]) -> None:
    if not levels:
        raise ValueError("order book vazio")
    if any(p <= 0 or q < 0 for p, q in levels):
        raise ValueError("order book com preço ou quantidade inválidos")


def simulate_market_buy(asks: Sequence[Level], quote_amount: float) -> FillEstimate:
    """Compra gastando `quote_amount` (em USDT ou R$), consumindo as ofertas de venda (asks)."""
    if quote_amount <= 0:
        raise ValueError("valor da ordem precisa ser positivo")
    _check_book(asks)
    best = asks[0][0]
    remaining, base, spent, used = quote_amount, 0.0, 0.0, 0
    for price, qty in asks:
        if remaining <= 0:
            break
        take = min(remaining, price * qty)
        if take <= 0:
            continue
        base += take / price
        spent += take
        remaining -= take
        used += 1
    avg = spent / base if base > 0 else None
    slip = max(0.0, (avg / best - 1) * 100) if avg else None  # max: só ruído de ponto flutuante abaixo de 0
    return FillEstimate("buy", best, avg, base, spent, slip, remaining <= quote_amount * 1e-9, used)


def simulate_market_sell(bids: Sequence[Level], base_qty: float) -> FillEstimate:
    """Venda de `base_qty` (em ETH, por exemplo), consumindo as ofertas de compra (bids)."""
    if base_qty <= 0:
        raise ValueError("quantidade precisa ser positiva")
    _check_book(bids)
    best = bids[0][0]
    remaining, sold, received, used = base_qty, 0.0, 0.0, 0
    for price, qty in bids:
        if remaining <= 0:
            break
        take = min(remaining, qty)
        if take <= 0:
            continue
        sold += take
        received += take * price
        remaining -= take
        used += 1
    avg = received / sold if sold > 0 else None
    slip = max(0.0, (1 - avg / best) * 100) if avg else None
    return FillEstimate("sell", best, avg, sold, received, slip, remaining <= base_qty * 1e-9, used)


def spread_pct(bid: float, ask: float) -> float:
    """Spread em % do preço médio (meio entre compra e venda)."""
    if bid <= 0 or ask <= 0:
        raise ValueError("preços precisam ser positivos")
    if ask < bid:
        raise ValueError(f"livro cruzado: ask {ask} < bid {bid}")
    return (ask - bid) / ((ask + bid) / 2) * 100


def depth_quote(levels: Sequence[Level], n: int) -> float:
    """Volume (em moeda de cotação) disponível nos primeiros `n` níveis."""
    return sum(p * q for p, q in levels[:n])

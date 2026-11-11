"""Regras de execução simulada, usadas IGUAIS no backtest e no paper trading.

- Compra a mercado: preço × (1 + meio spread + slippage); taxa sobre o valor.
- Venda a mercado: preço × (1 − meio spread − slippage); taxa sobre o valor.
- OCO (stop + alvo 1) conferida candle a candle de 1m:
  * stop: executa a mercado com slippage maior; se o candle abre abaixo do stop, executa na abertura;
  * alvo: ordem limitada, executa no alvo (ou na abertura, se abrir acima);
  * stop e alvo no mesmo candle: conta o stop (pior caso, porque não dá para saber a ordem).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def buy_fill(price: float, half_spread: float, slip_pct: float) -> float:
    """Preço pago numa compra a mercado: preço × (1 + meio spread + slippage)."""
    return price * (1 + half_spread + slip_pct / 100)


def sell_fill(price: float, half_spread: float, slip_pct: float) -> float:
    """Preço recebido numa venda a mercado: preço × (1 − meio spread − slippage)."""
    return price * (1 - half_spread - slip_pct / 100)


@dataclass(frozen=True)
class OcoHit:
    """Execução da OCO: em qual candle, a que preço e por qual motivo ("stop" ou "alvo1")."""

    index: int  # posição do candle de 1m (dentro dos arrays passados)
    fill: float
    reason: str  # "stop" | "alvo1"


def oco_check(
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    *,
    stop: float,
    target: float,
    half_spread: float,
    slip_stop_pct: float,
) -> OcoHit | None:
    """Procura, nos candles de 1m dados, a primeira execução do stop ou do alvo. No mesmo candle, conta o stop. Devolve None se nenhum foi atingido."""
    hit_stop = lows <= stop
    hit_tgt = highs >= target
    s = int(np.argmax(hit_stop)) if hit_stop.any() else None
    g = int(np.argmax(hit_tgt)) if hit_tgt.any() else None
    if s is not None and (g is None or s <= g):
        return OcoHit(s, float(min(opens[s], stop) * (1 - slip_stop_pct / 100 - half_spread)), "stop")
    if g is not None:
        return OcoHit(g, float(max(opens[g], target)), "alvo1")
    return None


def close_pnl(qty: float, size: float, fee_in: float, exit_fill: float, fee: float) -> tuple[float, float]:
    """(resultado líquido, taxa de saída)."""
    gross = qty * exit_fill
    fee_out = gross * fee
    return gross - fee_out - size - fee_in, fee_out

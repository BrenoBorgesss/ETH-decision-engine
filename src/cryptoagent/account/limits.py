"""Limites do dia: quanto já perdeu, quanto resta e o risco por operação."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LimitStatus:
    capital_brl: float
    max_loss_brl: float
    pnl_brl: float
    pnl_pct: float
    loss_used_brl: float  # quanto da perda máxima já foi consumido
    remaining_brl: float  # quanto ainda pode perder hoje
    risk_per_trade_brl: float  # perda máxima aceitável por operação (se o stop for atingido)
    blocked: bool

    @property
    def used_pct(self) -> float:
        return min(100.0, self.loss_used_brl / self.max_loss_brl * 100) if self.max_loss_brl else 100.0


def evaluate_limits(capital_brl: float, max_loss_brl: float, pnl_brl: float, *, divisor: float, profit_extends: bool = False) -> LimitStatus:
    """
    - Perda consumida = quanto o resultado do dia está negativo.
    - Com `profit_extends=False` (padrão), lucro do dia NÃO aumenta a perda permitida.
    - Risco por operação = perda máxima ÷ divisor, mas nunca maior que o que resta.
    - Bloqueia (só ESPERAR) quando não resta nada.
    """
    if capital_brl <= 0 or max_loss_brl <= 0:
        raise ValueError("capital e perda máxima precisam ser positivos")
    loss_used = max(0.0, -pnl_brl)
    remaining = max_loss_brl + pnl_brl if profit_extends else max_loss_brl - loss_used
    remaining = max(0.0, remaining)
    return LimitStatus(
        capital_brl=capital_brl,
        max_loss_brl=max_loss_brl,
        pnl_brl=pnl_brl,
        pnl_pct=pnl_brl / capital_brl * 100,
        loss_used_brl=loss_used,
        remaining_brl=remaining,
        risk_per_trade_brl=min(max_loss_brl / divisor, remaining),
        blocked=remaining <= 1e-9,
    )

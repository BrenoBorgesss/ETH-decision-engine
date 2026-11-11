"""O que o motor de decisão enxerga em um instante.

Tudo aqui é "foto até agora": só candles já FECHADOS antes do instante da decisão.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from cryptoagent.indicators.structure import Level


def closed_rows(frame: pd.DataFrame, t_ms: int, n: int) -> pd.DataFrame:
    """Últimos `n` candles com fechamento ANTES de `t_ms` (sem olhar o futuro)."""
    close_times = frame["close_time"].to_numpy()
    end = int(np.searchsorted(close_times, t_ms, side="left"))
    return frame.iloc[max(0, end - n) : end]


@dataclass(frozen=True)
class MarketView:
    time_ms: int
    price: float  # preço de referência agora (no par de análise)
    frames: dict[str, pd.DataFrame]  # timeframe -> últimos candles fechados, com indicadores
    levels: list[Level] = field(default_factory=list)  # suportes/resistências (no par de análise)
    spread_pct: float | None = None  # spread atual do par de operação
    book_imbalance: float | None = None  # só ao vivo
    funding_rate: float | None = None


@dataclass(frozen=True)
class PositionView:
    """Posição DA OPERAÇÃO aberta, com preços já no par de análise."""

    qty: float
    avg_cost: float
    opened_ms: int
    stop: float | None = None
    target1: float | None = None
    target2: float | None = None
    source: str = "regras"  # "sinal #12" quando veio de um sinal associado


@dataclass(frozen=True)
class RiskView:
    blocked_reasons: list[str]  # conta, sessão, perda do dia, saúde dos dados...
    risk_per_trade_brl: float | None
    capital_brl: float | None
    data_warnings: list[str] = field(default_factory=list)  # avisos não críticos (ex.: derivativos fora)
    # Perda máxima do dia atingida. Fica separada das outras travas porque, com posição aberta,
    # ainda permite VENDER nas saídas que reduzem risco (stop, invalidação, tempo).
    daily_limit_reason: str | None = None


@dataclass(frozen=True)
class SeasonView:
    factor: float = 1.0
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DecisionContext:
    symbol: str  # par de análise (onde os sinais são calculados)
    trade_symbol: str  # par de operação (onde você executa)
    trade_price_ratio: float  # preço do par de operação ÷ preço do par de análise (para traduzir níveis)
    market: MarketView
    risk: RiskView
    cost_pct: float  # custo de ida e volta (ponto de equilíbrio) no par de operação, em %
    position: PositionView | None = None
    season: SeasonView = field(default_factory=SeasonView)
    live: bool = True  # False no backtest

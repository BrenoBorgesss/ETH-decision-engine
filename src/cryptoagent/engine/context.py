"""O que o motor de decisão enxerga em um instante.

Tudo aqui é "foto até agora": só candles já FECHADOS antes do instante da decisão.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from cryptoagent.indicators.structure import Level


def closed_rows(frame: pd.DataFrame, t_ms: int, n: int) -> pd.DataFrame:
    """Últimos `n` candles com fechamento ANTES de `t_ms` (sem olhar o futuro)."""
    close_times = frame["close_time"].to_numpy()
    end = int(np.searchsorted(close_times, t_ms, side="left"))
    return frame.iloc[max(0, end - n) : end]


class ArrayRow:
    """Uma linha de candle lida direto dos arrays de cada coluna (sem montar uma Series do pandas).

    Montar a linha com `frame.iloc[-1]` custa dezenas de microssegundos; no backtest isso é feito
    centenas de milhares de vezes. Aqui cada valor é lido só quando pedido, em tempo constante.
    """

    __slots__ = ("_columns", "_index")

    def __init__(self, columns: Mapping[str, Any], index: int) -> None:
        self._columns = columns
        self._index = index

    def __getitem__(self, column: str) -> Any:  # noqa: ANN401 - o tipo depende da coluna
        return self._columns[column][self._index]


def nan_min(values: np.ndarray) -> float:
    """Menor valor ignorando NaN (como o `.min()` do pandas); NaN se não sobrar nenhum."""
    valid = values[~np.isnan(values)]
    return float(valid.min()) if valid.size else float("nan")


@dataclass(frozen=True)
class MarketView:
    """O mercado no instante da decisão: preço, candles fechados com indicadores, suportes e resistências, spread e extras."""
    time_ms: int
    price: float  # preço de referência agora (no par de análise)
    frames: dict[str, pd.DataFrame]  # timeframe -> últimos candles fechados, com indicadores
    levels: list[Level] = field(default_factory=list)  # suportes/resistências (no par de análise)
    spread_pct: float | None = None  # spread atual do par de operação
    book_imbalance: float | None = None  # só ao vivo
    funding_rate: float | None = None
    # Opcional: leitor rápido de linhas (timeframe, quantos candles para trás) -> linha. O backtest fornece um
    # baseado em arrays; sem ele, a linha vem de `frames`.
    rows: Callable[[str, int], Any] | None = None
    tails: Callable[[str, str, int], np.ndarray] | None = None  # (timeframe, coluna, n) -> últimos n valores

    def last(self, tf: str, back: int = 1) -> Any:  # noqa: ANN401 - Series do pandas ou ArrayRow
        """Linha do candle fechado mais recente de `tf` (`back=1`) ou do anterior (`back=2`). Aceita `linha["coluna"]`."""
        if self.rows is not None:
            return self.rows(tf, back)
        return self.frames[tf].iloc[-back]

    def tail(self, tf: str, column: str, n: int) -> np.ndarray:
        """Os últimos `n` valores (ou menos, se não houver tantos) de uma coluna de `tf`, do mais antigo ao mais recente."""
        if self.tails is not None:
            return self.tails(tf, column, n)
        return self.frames[tf][column].to_numpy()[-n:]


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
    """O lado de risco da decisão: motivos de bloqueio, risco por operação, capital, avisos de dados e perda máxima do dia."""
    blocked_reasons: list[str]  # conta, sessão, perda do dia, saúde dos dados...
    risk_per_trade_brl: float | None
    capital_brl: float | None
    data_warnings: list[str] = field(default_factory=list)  # avisos não críticos (ex.: derivativos fora)
    # Perda máxima do dia atingida. Fica separada das outras travas porque, com posição aberta,
    # ainda permite VENDER nas saídas que reduzem risco (stop, invalidação, tempo).
    daily_limit_reason: str | None = None
    # Estratégia ainda não validada no backtest (regra de confiabilidade): compras viram AGUARDAR.
    # Não afeta vendas. Vazio no backtest e no paper trading, que são justamente onde ela é testada.
    validation_reason: str | None = None


@dataclass(frozen=True)
class SeasonView:
    """Efeito do horário na confiança: fator multiplicador e os motivos."""
    factor: float = 1.0
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DecisionContext:
    """Tudo que o motor de decisão recebe. É montado igual no modo ao vivo e no backtest."""
    symbol: str  # par de análise (onde os sinais são calculados)
    trade_symbol: str  # par de operação (onde você executa)
    trade_price_ratio: float  # preço do par de operação ÷ preço do par de análise (para traduzir níveis)
    market: MarketView
    risk: RiskView
    cost_pct: float  # custo de ida e volta (ponto de equilíbrio) no par de operação, em %
    position: PositionView | None = None
    season: SeasonView = field(default_factory=SeasonView)
    live: bool = True  # False no backtest

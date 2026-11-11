"""Cache dos indicadores na análise ao vivo: não recalcula um timeframe cujos candles não mudaram.

A análise roda a cada fechamento do candle de 5m. Entre uma e outra, os candles FECHADOS de
15m, 1h e 4h quase sempre são os mesmos, e recalcular todos os indicadores deles é trabalho
repetido. Aqui o resultado é guardado junto com uma "impressão digital" dos candles; se a
impressão for a mesma, devolve uma cópia do resultado guardado.

Candle fechado não muda, então a impressão digital usa a quantidade de candles, o primeiro e o
último horário e as somas de fechamento e volume (pegam qualquer troca de valor no meio).
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Hashable

import pandas as pd

from cryptoagent.config import Indicadores, Regime
from cryptoagent.indicators.compute import compute_indicators


def fingerprint(df: pd.DataFrame) -> tuple:
    """Impressão digital barata de uma tabela de candles fechados."""
    if df.empty:
        return (0,)
    return (
        len(df),
        int(df["open_time"].iloc[0]),
        int(df["close_time"].iloc[-1]),
        float(df["close"].to_numpy().sum()),
        float(df["volume"].to_numpy().sum()),
    )


class IndicatorCache:
    """Guarda o último resultado de cada chave (ex.: par + timeframe). Acertos e erros ficam contados."""

    def __init__(self, max_entries: int = 64) -> None:
        self.max_entries = max_entries
        self._items: OrderedDict[Hashable, tuple[tuple, pd.DataFrame]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def compute(
        self, key: Hashable, df: pd.DataFrame, p: Indicadores, r: Regime, *, tz_name: str, day_start: str
    ) -> pd.DataFrame:
        """Indicadores de `df`, reaproveitando o cálculo anterior da mesma chave se os candles e os parâmetros forem os mesmos."""
        mark = (fingerprint(df), p.model_dump_json(), r.model_dump_json(), tz_name, day_start)
        hit = self._items.get(key)
        if hit is not None and hit[0] == mark:
            self.hits += 1
            self._items.move_to_end(key)
            return hit[1].copy()
        self.misses += 1
        out = compute_indicators(df, p, r, tz_name=tz_name, day_start=day_start)
        self._items[key] = (mark, out)
        self._items.move_to_end(key)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)
        return out.copy()

    def clear(self) -> None:
        """Esvazia o cache e zera os contadores."""
        self._items.clear()
        self.hits = self.misses = 0

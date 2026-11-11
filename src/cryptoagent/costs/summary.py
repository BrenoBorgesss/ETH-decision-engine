"""Resumo rápido da coleta de custos (quantos dias, cobertura, spread).

O relatório completo (slippage por hora, custo de ida e volta, recomendação de par)
vem numa etapa posterior, quando houver dias suficientes de coleta.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import pandas as pd

from cryptoagent.timeutils import local_trading_day_series


@dataclass(frozen=True)
class SymbolCoverage:
    """Resumo da coleta de custos de um par: amostras, período, dias cobertos, cobertura e estatísticas do spread."""
    symbol: str
    samples: int
    first_ms: int
    last_ms: int
    local_days: int
    weekend_days: int
    coverage_pct: float  # amostras recebidas / amostras esperadas no período
    rest_share_pct: float
    spread_mean_pct: float
    spread_median_pct: float
    spread_p95_pct: float


def collection_summary(conn: sqlite3.Connection, tz_name: str, day_start: str, sample_every_s: float) -> list[SymbolCoverage]:
    """Resumo da coleta por par. `sample_every_s` é o intervalo esperado entre amostras, usado para calcular a cobertura."""
    df = pd.read_sql_query("SELECT ts_ms, symbol, source, spread_pct FROM book_samples", conn)
    if df.empty:
        return []
    df["day"] = local_trading_day_series(df["ts_ms"], tz_name, day_start)
    out = []
    for symbol, g in df.groupby("symbol", sort=True):
        first, last = int(g["ts_ms"].min()), int(g["ts_ms"].max())
        expected = max(1.0, (last - first) / 1000 / sample_every_s + 1)
        days = pd.Series(sorted(set(g["day"])))
        weekend = int(sum(1 for d in days if d.weekday() >= 5))
        out.append(
            SymbolCoverage(
                symbol=str(symbol),
                samples=len(g),
                first_ms=first,
                last_ms=last,
                local_days=len(days),
                weekend_days=weekend,
                coverage_pct=min(100.0, len(g) / expected * 100),
                rest_share_pct=float((g["source"] == "rest").mean() * 100),
                spread_mean_pct=float(g["spread_pct"].mean()),
                spread_median_pct=float(g["spread_pct"].median()),
                spread_p95_pct=float(g["spread_pct"].quantile(0.95)),
            )
        )
    return out

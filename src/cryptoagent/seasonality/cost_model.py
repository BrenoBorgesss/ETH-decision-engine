"""Custo de ida e volta usado na análise por horário, para cada par.

Taxa: a do config (conservadora). O spread vem da coleta ao vivo quando ela existe; senão,
do valor padrão do config. O relatório sempre diz de onde veio cada número.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from cryptoagent.costs.roundtrip import LegCosts, round_trip


@dataclass(frozen=True)
class CostModel:
    """Custo de operar um par: taxa por lado, spread e quanto o preço precisa andar para empatar a ida e volta."""

    symbol: str
    fee_pct_side: float  # taxa por lado, em %
    spread_pct: float  # spread total (compra − venda), em % do preço
    breakeven_pct: float  # quanto o preço precisa andar para empatar a ida e volta
    spread_source: str


def measured_spread(db_path: Path, symbol: str) -> tuple[float, int] | None:
    """Spread mediano medido pelo coletor (em %) e quantas amostras existem."""
    if not db_path.is_file():
        return None
    conn = sqlite3.connect(db_path)
    try:
        rows = [
            r[0]
            for r in conn.execute(
                "SELECT spread_pct FROM book_samples WHERE symbol = ? AND spread_pct IS NOT NULL", (symbol,)
            )
        ]
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if not rows:
        return None
    rows.sort()
    return rows[len(rows) // 2], len(rows)


def build_cost_model(
    symbol: str, fee_side: float, default_spreads: dict[str, float], db_path: Path | None
) -> CostModel:
    """Monta o custo do par com o spread mediano da coleta ao vivo, ou com o valor padrão do config se ainda não há coleta."""
    measured = measured_spread(db_path, symbol) if db_path else None
    if measured:
        spread, n = measured
        source = f"mediana da coleta ao vivo ({n:,} amostras)"
    else:
        spread = default_spreads.get(symbol, 0.05)
        source = "valor padrão do config (ainda sem coleta ao vivo)"
    leg = LegCosts(fee=fee_side, half_spread=spread / 200)
    rt = round_trip(leg, leg)
    return CostModel(symbol, fee_side * 100, spread, rt.breakeven_pct, source)

"""Custos no backtest: taxa da conta, spread por hora do dia e slippage.

O order book não tem histórico. Então o spread de cada hora vem da MEDIANA medida pelo coletor
ao vivo naquela hora (quando há amostras suficientes); nas horas sem amostras, usa o valor padrão
do config. O relatório mostra quantas horas usaram medição real.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from cryptoagent.costs.roundtrip import LegCosts, round_trip
from cryptoagent.timeutils import local_hour_weekday_series


@dataclass(frozen=True)
class CostSchedule:
    symbol: str
    fee: float  # fração por lado
    spread_by_hour: dict[int, float]  # spread total em % por hora local
    measured_hours: list[int] = field(default_factory=list)
    slip_market_pct: float = 0.01
    slip_stop_pct: float = 0.05
    fee_source: str = ""

    def half_spread(self, hour: int) -> float:
        return self.spread_by_hour[hour] / 200  # fração

    def roundtrip_pct(self, hour: int) -> float:
        leg = LegCosts(fee=self.fee, half_spread=self.half_spread(hour), slippage=self.slip_market_pct / 100)
        return round_trip(leg, leg).breakeven_pct

    @property
    def mean_roundtrip_pct(self) -> float:
        return sum(self.roundtrip_pct(h) for h in range(24)) / 24


def hourly_spreads(db_path: Path | None, symbol: str, tz: str, min_samples: int) -> dict[int, float]:
    if db_path is None or not db_path.is_file():
        return {}
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query("SELECT ts_ms, spread_pct FROM book_samples WHERE symbol = ?", conn, params=(symbol,))
    except Exception:
        return {}
    finally:
        conn.close()
    if df.empty:
        return {}
    df["hour"] = local_hour_weekday_series(df["ts_ms"], tz)["hour"].to_numpy()
    g = df.groupby("hour")["spread_pct"]
    counts, medians = g.size(), g.median()
    return {int(h): float(medians[h]) for h in counts.index if counts[h] >= min_samples}


def build_schedule(
    symbol: str, fee: float, default_spread: float, db_path: Path | None, tz: str, *, min_samples: int, slip_market: float, slip_stop: float, fee_source: str
) -> CostSchedule:
    measured = hourly_spreads(db_path, symbol, tz, min_samples)
    spreads = {h: measured.get(h, default_spread) for h in range(24)}
    return CostSchedule(symbol, fee, spreads, sorted(measured), slip_market, slip_stop, fee_source)

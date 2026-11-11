"""Custos no backtest: taxa da conta, spread por hora do dia e slippage.

O order book não tem histórico. Então o spread de cada hora vem da MEDIANA medida pelo coletor
ao vivo naquela hora (quando há amostras suficientes); nas horas sem amostras, usa o valor padrão
do config. O relatório mostra quantas horas usaram medição real.

Reprodutibilidade: a taxa (lida da conta) e os spreads (banco do coletor, que cresce a cada
coleta) mudam com o tempo. `save_schedules` grava os custos usados num arquivo JSON e
`load_schedules` os lê de volta, para repetir um backtest com exatamente os mesmos custos.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from cryptoagent.costs.roundtrip import LegCosts, round_trip
from cryptoagent.timeutils import local_hour_weekday_series


@dataclass(frozen=True)
class CostSchedule:
    """Custos usados num backtest: taxa por lado, spread por hora do dia e slippage."""
    symbol: str
    fee: float  # fração por lado
    spread_by_hour: dict[int, float]  # spread total em % por hora local
    measured_hours: list[int] = field(default_factory=list)
    slip_market_pct: float = 0.01
    slip_stop_pct: float = 0.05
    fee_source: str = ""

    def half_spread(self, hour: int) -> float:
        """Metade do spread da hora, como fração do preço."""
        return self.spread_by_hour[hour] / 200  # fração

    def roundtrip_pct(self, hour: int) -> float:
        """Quanto o preço precisa andar (%) para empatar uma compra e uma venda naquela hora."""
        leg = LegCosts(fee=self.fee, half_spread=self.half_spread(hour), slippage=self.slip_market_pct / 100)
        return round_trip(leg, leg).breakeven_pct

    @property
    def mean_roundtrip_pct(self) -> float:
        """Média do custo de ida e volta nas 24 horas."""
        return sum(self.roundtrip_pct(h) for h in range(24)) / 24

    def to_dict(self) -> dict:
        """Forma simples (só tipos de JSON) para gravar em arquivo."""
        return {
            "symbol": self.symbol,
            "fee": self.fee,
            "spread_by_hour": {str(h): self.spread_by_hour[h] for h in sorted(self.spread_by_hour)},
            "measured_hours": list(self.measured_hours),
            "slip_market_pct": self.slip_market_pct,
            "slip_stop_pct": self.slip_stop_pct,
            "fee_source": self.fee_source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CostSchedule:
        """Inverso de `to_dict`. Exige as 24 horas na tabela de spread."""
        raw = {int(h): float(v) for h, v in data["spread_by_hour"].items()}
        spreads = {h: raw[h] for h in sorted(raw)}  # ordem numérica (no JSON as chaves vêm como texto: "0", "1", "10"...)
        if list(spreads) != list(range(24)):
            raise ValueError(f"custos de {data.get('symbol')}: a tabela de spread precisa ter as 24 horas")
        return cls(
            symbol=str(data["symbol"]),
            fee=float(data["fee"]),
            spread_by_hour=spreads,
            measured_hours=[int(h) for h in data.get("measured_hours", [])],
            slip_market_pct=float(data["slip_market_pct"]),
            slip_stop_pct=float(data["slip_stop_pct"]),
            fee_source=str(data.get("fee_source", "")),
        )


SCHEDULE_FILE_VERSION = 1


def save_schedules(path: Path, schedules: list[CostSchedule]) -> None:
    """Grava os custos de cada par num JSON legível (chaves ordenadas), para congelar um backtest."""
    payload = {"versao": SCHEDULE_FILE_VERSION, "pares": {s.symbol: s.to_dict() for s in schedules}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_schedules(path: Path) -> dict[str, CostSchedule]:
    """Lê um arquivo gravado por `save_schedules`. Devolve {par: custos}."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("versao") != SCHEDULE_FILE_VERSION:
        raise ValueError(f"arquivo de custos {path} com versão desconhecida: {payload.get('versao')!r}")
    return {sym: CostSchedule.from_dict(data) for sym, data in payload["pares"].items()}


def hourly_spreads(db_path: Path | None, symbol: str, tz: str, min_samples: int) -> dict[int, float]:
    """Mediana do spread medido pelo coletor em cada hora local, só para as horas com pelo menos `min_samples` amostras. Vazio se não houver banco."""
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
    """Monta os custos de um par: spread medido nas horas com amostras suficientes e o valor padrão nas demais."""
    measured = hourly_spreads(db_path, symbol, tz, min_samples)
    spreads = {h: measured.get(h, default_spread) for h in range(24)}
    return CostSchedule(symbol, fee, spreads, sorted(measured), slip_market, slip_stop, fee_source)

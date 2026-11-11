"""Estruturas de dados. Todo dado sabe de onde veio (fonte) e quando chegou (timestamp UTC)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

import numpy as np
import pandas as pd

from cryptoagent.timeutils import interval_ms, normalize_epoch_ms_array

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Stamped(Generic[T]):
    value: T
    source: str  # ex.: "binance-rest:/api/v3/ticker/bookTicker"
    received_ms: int  # quando chegou aqui (UTC, relógio local)
    exchange_ms: int | None = None  # horário informado pela corretora, quando existe
    latency_ms: float | None = None

    def age_s(self, now_ms: int) -> float:
        return (now_ms - self.received_ms) / 1000


# Colunas dos candles (mesma ordem da Binance; a 12ª coluna "ignore" é descartada).
KLINE_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trades",
    "taker_buy_base",  # volume comprado por ordens a mercado (comprador agressivo)
    "taker_buy_quote",
]
_FLOAT_COLUMNS = ["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_base", "taker_buy_quote"]


def empty_klines() -> pd.DataFrame:
    df = pd.DataFrame({c: pd.Series(dtype="float64") for c in KLINE_COLUMNS})
    return df.astype({"open_time": "int64", "close_time": "int64", "trades": "int64"})


def normalize_klines(df: pd.DataFrame) -> pd.DataFrame:
    """Tipos corretos, horários em ms UTC, sem duplicatas, ordenado por horário."""
    if df.empty:
        return empty_klines()
    out = df.loc[:, KLINE_COLUMNS].copy()
    out["open_time"] = normalize_epoch_ms_array(out["open_time"].to_numpy())
    out["close_time"] = normalize_epoch_ms_array(out["close_time"].to_numpy())
    out[_FLOAT_COLUMNS] = out[_FLOAT_COLUMNS].astype("float64")
    out["trades"] = out["trades"].astype("int64")
    out = out.drop_duplicates("open_time", keep="last").sort_values("open_time")
    return out.reset_index(drop=True)


def klines_to_frame(rows: list[list]) -> pd.DataFrame:
    """Converte a resposta de /api/v3/klines em DataFrame."""
    if not rows:
        return empty_klines()
    return normalize_klines(pd.DataFrame([r[:11] for r in rows], columns=KLINE_COLUMNS))


@dataclass(frozen=True, slots=True)
class Gap:
    start_ms: int  # open_time do primeiro candle que falta
    end_ms: int  # open_time do candle que volta a existir
    missing: int  # quantos candles faltam


def find_gaps(df: pd.DataFrame, interval: str) -> list[Gap]:
    """Buracos na sequência de candles (ex.: manutenção da corretora ou falha de download)."""
    if len(df) < 2:
        return []
    step = interval_ms(interval)
    times = df["open_time"].to_numpy(dtype="int64")
    diffs = np.diff(times)
    idx = np.nonzero(diffs > step)[0]
    return [Gap(int(times[i] + step), int(times[i + 1]), int(diffs[i] // step - 1)) for i in idx]

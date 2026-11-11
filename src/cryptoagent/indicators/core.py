"""Médias básicas: SMA, EMA e RMA (média de Wilder)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def _check_period(n: int) -> None:
    if n < 1:
        raise ValueError("período precisa ser >= 1")


def sma(values: pd.Series, n: int) -> pd.Series:
    _check_period(n)
    return values.rolling(n, min_periods=n).mean()


def _seeded_ewm(values: pd.Series, n: int, alpha: float) -> pd.Series:
    """Média exponencial com semente = média simples dos primeiros n valores válidos."""
    _check_period(n)
    out = pd.Series(np.nan, index=values.index, dtype="float64")
    valid = values.dropna()
    if len(valid) < n:
        return out
    seed_pos = values.index.get_loc(valid.index[n - 1])
    tail = values.iloc[seed_pos:].astype("float64").copy()
    tail.iloc[0] = valid.iloc[:n].mean()
    out.iloc[seed_pos:] = tail.ewm(alpha=alpha, adjust=False).mean().to_numpy()
    return out


def ema(values: pd.Series, n: int) -> pd.Series:
    """EMA clássica: alfa = 2 / (n + 1)."""
    return _seeded_ewm(values, n, 2.0 / (n + 1))


def rma(values: pd.Series, n: int) -> pd.Series:
    """Média de Wilder (usada em RSI, ATR e ADX): alfa = 1 / n."""
    return _seeded_ewm(values, n, 1.0 / n)


def slope_pct(values: pd.Series, bars: int) -> pd.Series:
    """Inclinação: variação % da série em `bars` candles."""
    return (values / values.shift(bars) - 1) * 100

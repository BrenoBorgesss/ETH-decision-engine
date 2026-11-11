"""Volatilidade: True Range, ATR (Wilder) e Bandas de Bollinger."""

from __future__ import annotations

import pandas as pd

from cryptoagent.indicators.core import rma


def true_range(df: pd.DataFrame) -> pd.Series:
    """Maior entre: máxima − mínima, |máxima − fechamento anterior|, |mínima − fechamento anterior|.

    No primeiro candle (sem anterior) vale máxima − mínima.
    """
    prev_close = df["close"].shift(1)
    ranges = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1)
    return ranges.max(axis=1, skipna=True)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """ATR de Wilder: média de Wilder do True Range. Primeiro valor no candle n − 1."""
    return rma(true_range(df), n)


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    """Bandas de Bollinger com desvio-padrão populacional (como definido por John Bollinger).

    %B = (preço − banda inferior) / (superior − inferior): 0 = na banda inferior, 1 = na superior,
    abaixo de 0 = fechou fora, por baixo.  Largura = (superior − inferior) / meio.
    """
    mid = close.rolling(n, min_periods=n).mean()
    std = close.rolling(n, min_periods=n).std(ddof=0)
    upper, lower = mid + k * std, mid - k * std
    width = upper - lower
    return pd.DataFrame(
        {
            "bb_mid": mid,
            "bb_upper": upper,
            "bb_lower": lower,
            "bb_pctb": (close - lower) / width.where(width != 0),
            "bb_width": width / mid,
        },
        index=close.index,
    )


def rolling_percentile(values: pd.Series, window: int) -> pd.Series:
    """Percentil (0 a 100) do valor atual dentro da janela que termina nele (sem olhar o futuro)."""
    return values.rolling(window, min_periods=max(20, window // 4)).rank(pct=True) * 100

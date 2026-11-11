"""Tendência: EMAs e ADX (força da tendência, com +DI e -DI)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from cryptoagent.indicators.core import ema, rma
from cryptoagent.indicators.volatility import true_range


def emas(close: pd.Series, periods: list[int]) -> pd.DataFrame:
    """Uma coluna `ema_<n>` para cada período pedido."""
    return pd.DataFrame({f"ema_{n}": ema(close, n) for n in periods}, index=close.index)


def adx(df: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    """ADX de Wilder.

    +DM = alta de hoje acima da de ontem (se maior que a queda da mínima), senão 0.
    -DM = queda da mínima (se maior que a alta da máxima), senão 0.
    +DI / -DI = 100 × média de Wilder(DM) / média de Wilder(TR).
    DX = 100 × |+DI − −DI| / (+DI + −DI);  ADX = média de Wilder do DX.
    Primeiro ADX válido no candle 2n − 1.
    """
    high, low = df["high"], df["low"]
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    plus_dm.iloc[0] = np.nan
    minus_dm.iloc[0] = np.nan
    tr = true_range(df).copy()
    tr.iloc[0] = np.nan  # o primeiro candle não tem "ontem": fica fora, como em Wilder

    atr_ = rma(tr, n)
    plus_di = 100 * rma(plus_dm, n) / atr_
    minus_di = 100 * rma(minus_dm, n) / atr_
    di_sum = plus_di + minus_di
    dx = 100 * (plus_di - minus_di).abs() / di_sum.where(di_sum != 0)
    return pd.DataFrame({"plus_di": plus_di, "minus_di": minus_di, "dx": dx, "adx": rma(dx, n)}, index=df.index)

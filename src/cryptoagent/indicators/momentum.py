"""Momentum: RSI (Wilder), MACD e Stochastic RSI."""

from __future__ import annotations

import pandas as pd

from cryptoagent.indicators.core import ema, rma, sma


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """RSI de Wilder. Primeiro valor no candle n (precisa de n variações).

    RSI = 100 − 100 / (1 + média_ganhos / média_perdas). Sem perdas = 100; sem ganhos = 0.
    """
    delta = close.diff()
    gains = delta.clip(lower=0)
    losses = (-delta).clip(lower=0)
    avg_gain = rma(gains, n)
    avg_loss = rma(losses, n)
    out = 100 - 100 / (1 + avg_gain / avg_loss)
    out = out.where(avg_loss != 0, 100.0)
    out = out.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)  # preço parado: neutro
    return out.where(avg_gain.notna())


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": line - sig}, index=close.index)


def stoch_rsi(close: pd.Series, rsi_n: int = 14, stoch_n: int = 14, k: int = 3, d: int = 3) -> pd.DataFrame:
    """Stochastic RSI: onde o RSI está dentro da própria faixa recente (0 a 100).

    %K = média simples (k) do estocástico do RSI; %D = média simples (d) do %K.
    Se o RSI ficou parado na janela (faixa zero), vale 50 (neutro).
    """
    r = rsi(close, rsi_n)
    lo = r.rolling(stoch_n, min_periods=stoch_n).min()
    hi = r.rolling(stoch_n, min_periods=stoch_n).max()
    rng = hi - lo
    raw = ((r - lo) / rng.where(rng != 0)) * 100
    raw = raw.where(rng != 0, 50.0).where(r.notna() & lo.notna())
    k_line = sma(raw, k)
    return pd.DataFrame({"stochrsi_k": k_line, "stochrsi_d": sma(k_line, d)}, index=close.index)

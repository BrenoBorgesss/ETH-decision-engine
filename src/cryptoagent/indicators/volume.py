"""Volume e liquidez: VWAP do dia, volume relativo, fluxo agressor (taker) e order book."""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from cryptoagent.timeutils import local_trading_day_series


def vwap_daily(df: pd.DataFrame, tz_name: str = "America/Sao_Paulo", day_start: str = "00:00") -> pd.DataFrame:
    """VWAP que recomeça todo dia às `day_start` no fuso local (padrão: 00:00 de Brasília).

    Preço típico = (máxima + mínima + fechamento) / 3. VWAP = Σ(preço típico × volume) / Σ volume
    desde o início do dia. Também devolve o desvio-padrão ponderado por volume (bandas do VWAP).
    """
    day = local_trading_day_series(df["open_time"], tz_name, day_start)
    typical = (df["high"] + df["low"] + df["close"]) / 3
    vol = df["volume"]
    cum_v = vol.groupby(day).cumsum()
    cum_pv = (typical * vol).groupby(day).cumsum()
    cum_p2v = (typical * typical * vol).groupby(day).cumsum()
    vwap = cum_pv / cum_v.where(cum_v != 0)
    var = (cum_p2v / cum_v.where(cum_v != 0) - vwap**2).clip(lower=0)
    return pd.DataFrame({"vwap": vwap, "vwap_std": var**0.5, "trading_day": day}, index=df.index)


def relative_volume(volume: pd.Series, n: int = 20) -> pd.Series:
    """Volume do candle ÷ média dos n candles ANTERIORES (o atual não entra na média)."""
    base = volume.rolling(n, min_periods=n).mean().shift(1)
    return volume / base.where(base != 0)


def taker_flow(df: pd.DataFrame, n: int = 6) -> pd.DataFrame:
    """Fluxo agressor: quanto do volume foi de quem comprou/vendeu a mercado.

    - taker_sell_ratio: fração do volume vinda de vendedores agressivos (0 a 1).
    - taker_sell_vol: volume vendido a mercado.
    - sell_vol_vs_peak: volume vendido agora ÷ maior volume vendido nos últimos n candles
      (bem abaixo de 1 depois de um pico = força vendedora perdendo fôlego).
    Os candles da Binance trazem o volume comprador agressivo, então isto PODE ser testado no histórico.
    """
    vol = df["volume"]
    sell_vol = (vol - df["taker_buy_base"]).clip(lower=0)
    peak = sell_vol.rolling(n, min_periods=n).max()
    return pd.DataFrame(
        {
            "taker_sell_ratio": sell_vol / vol.where(vol != 0),
            "taker_sell_vol": sell_vol,
            "sell_vol_vs_peak": sell_vol / peak.where(peak != 0),
        },
        index=df.index,
    )


def book_imbalance(bids: Sequence[tuple[float, float]], asks: Sequence[tuple[float, float]], levels: int = 10) -> float:
    """Desequilíbrio do order book nos primeiros níveis, em valor (preço × quantidade).

    +1 = só ordens de compra; −1 = só ordens de venda; 0 = equilibrado.
    SÓ AO VIVO: a Binance não guarda histórico do livro, então isto não entra no backtest.
    """
    b = sum(p * q for p, q in bids[:levels])
    a = sum(p * q for p, q in asks[:levels])
    total = a + b
    return (b - a) / total if total > 0 else 0.0

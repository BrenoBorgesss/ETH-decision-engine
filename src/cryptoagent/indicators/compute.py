"""Calcula todos os indicadores de uma vez para um DataFrame de candles."""

from __future__ import annotations

import pandas as pd

from cryptoagent.config import Indicadores, Regime
from cryptoagent.indicators.core import slope_pct
from cryptoagent.indicators.momentum import macd, rsi, stoch_rsi
from cryptoagent.indicators.regime import classify_regime
from cryptoagent.indicators.structure import divergences, market_structure, swing_points, wick_rejection
from cryptoagent.indicators.trend import adx, emas
from cryptoagent.indicators.volatility import atr, bollinger, rolling_percentile
from cryptoagent.indicators.volume import relative_volume, taker_flow, vwap_daily


def compute_indicators(
    df: pd.DataFrame,
    p: Indicadores,
    r: Regime,
    *,
    tz_name: str = "America/Sao_Paulo",
    day_start: str = "00:00",
) -> pd.DataFrame:
    """Devolve os candles + uma coluna por indicador. Nenhuma coluna usa dados futuros."""
    df = df.reset_index(drop=True)
    close = df["close"]
    parts = [df, emas(close, p.ema)]
    out = pd.concat(parts, axis=1)
    for n in p.ema:
        out[f"ema_{n}_slope"] = slope_pct(out[f"ema_{n}"], p.inclinacao_ema_barras)

    out["rsi"] = rsi(close, p.rsi)
    out = out.join(macd(close, *p.macd))
    out = out.join(stoch_rsi(close, *p.stoch_rsi))

    out["atr"] = atr(df, p.atr)
    out["atr_pct"] = out["atr"] / close * 100
    out["atr_pctl"] = rolling_percentile(out["atr_pct"], p.janela_percentil)
    out = out.join(bollinger(close, p.bollinger.periodo, p.bollinger.desvios))
    out["bb_width_pctl"] = rolling_percentile(out["bb_width"], p.janela_percentil)

    out = out.join(adx(df, p.adx))

    out = out.join(vwap_daily(df, tz_name, day_start))
    out["dist_vwap_atr"] = (close - out["vwap"]) / out["atr"]  # esticamento: quantos ATRs longe do VWAP
    out["rvol"] = relative_volume(df["volume"], p.volume_relativo)
    out = out.join(taker_flow(df, p.fluxo_taker_barras))

    swings = swing_points(df, p.pivo_barras)
    out = out.join(swings).join(market_structure(swings))
    out = out.join(divergences(df, swings, out["rsi"], p.pivo_barras, p.divergencia_max_barras))
    out = out.join(wick_rejection(df, p.pavio_min_proporcao))

    out["regime"] = classify_regime(
        out["adx"],
        out["plus_di"],
        out["minus_di"],
        out["atr_pctl"],
        adx_tendencia=r.adx_tendencia,
        adx_lateral=r.adx_lateral,
        atr_percentil_alta_vol=r.atr_percentil_alta_vol,
    )
    return out

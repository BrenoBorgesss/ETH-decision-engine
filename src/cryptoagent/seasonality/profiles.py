"""Perfis por hora do dia e dia da semana (horário de Brasília).

Métricas de cada candle:
- amplitude (range_pct) = (máxima − mínima) / abertura × 100: o MAIOR movimento possível
  dentro do candle. Ninguém captura a amplitude inteira, então "passar do custo" quer dizer
  "há espaço", não "há lucro".
- retorno absoluto (absret_pct) = |fechamento / abertura − 1| × 100.
- ATR% e volume (em moeda de cotação).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from cryptoagent.indicators.volatility import atr
from cryptoagent.timeutils import local_hour_weekday_series

WEEKDAYS_PT = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
CLASSES = ("compensa", "neutro", "nao_compensa")


def add_buckets(df: pd.DataFrame, tz_name: str = "America/Sao_Paulo", atr_n: int = 14) -> pd.DataFrame:
    """Acrescenta hora/dia da semana locais e as métricas de movimento de cada candle."""
    out = df.reset_index(drop=True).copy()
    hw = local_hour_weekday_series(out["open_time"], tz_name)
    out["hour"] = hw["hour"].to_numpy()
    out["weekday"] = hw["weekday"].to_numpy()
    out["range_pct"] = (out["high"] - out["low"]) / out["open"] * 100
    out["absret_pct"] = (out["close"] / out["open"] - 1).abs() * 100
    out["atr_pct"] = atr(out, atr_n) / out["close"] * 100
    return out


def bucket_stats(df: pd.DataFrame, keys: list[str], cost_pct: float, folga: float) -> pd.DataFrame:
    """Estatísticas por grupo (ex.: ["hour"], ["weekday"], ["hour", "weekday"]).

    pct_above_cost = % dos candles cuja amplitude passou de custo × folga.
    """
    threshold = cost_pct * folga
    data = df.assign(_above=df["range_pct"] > threshold)
    g = data.groupby(keys, sort=True)
    return pd.DataFrame(
        {
            "n": g.size(),
            "range_mean": g["range_pct"].mean(),
            "range_median": g["range_pct"].median(),
            "absret_mean": g["absret_pct"].mean(),
            "atr_pct_mean": g["atr_pct"].mean(),
            "quote_vol_mean": g["quote_volume"].mean(),
            "pct_above_cost": g["_above"].mean() * 100,
        }
    )


def classify(stats: pd.DataFrame, pct_min: float, pct_max: float) -> pd.Series:
    """compensa / neutro / nao_compensa pela % de candles que passam do custo com folga."""
    pct = stats["pct_above_cost"]
    return pd.Series(np.select([pct >= pct_min, pct <= pct_max], ["compensa", "nao_compensa"], "neutro"), index=stats.index)


def volatility_deviation(stats: pd.DataFrame, overall_median: float) -> pd.Series:
    """Quanto a amplitude mediana do grupo se afasta da mediana geral (0,10 = 10% acima)."""
    return stats["range_median"] / overall_median - 1

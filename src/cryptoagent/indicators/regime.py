"""Regime de mercado: tendência, lateral ou alta volatilidade, com regras explícitas.

Ordem das regras (a primeira que bater vale):
1. ATR% no percentil >= `atr_percentil_alta_vol` da janela  -> alta_volatilidade
2. ADX >= `adx_tendencia`: +DI > -DI -> tendencia_alta; senão -> tendencia_baixa
3. ADX <= `adx_lateral`                                     -> lateral
4. entre os dois limites                                    -> transicao
Faltando dado (aquecimento)                                 -> indefinido
"""

from __future__ import annotations

import numpy as np
import pandas as pd

REGIMES = ("tendencia_alta", "tendencia_baixa", "lateral", "alta_volatilidade", "transicao", "indefinido")


def classify_regime(
    adx: pd.Series,
    plus_di: pd.Series,
    minus_di: pd.Series,
    atr_pctl: pd.Series,
    *,
    adx_tendencia: float = 25,
    adx_lateral: float = 20,
    atr_percentil_alta_vol: float = 90,
) -> pd.Series:
    """Classifica cada candle em tendência de alta, tendência de baixa, lateral, alta volatilidade, transição ou indefinido, a partir do ADX, dos DI e do percentil do ATR."""
    if adx_lateral > adx_tendencia:
        raise ValueError("adx_lateral precisa ser <= adx_tendencia")
    known = adx.notna() & plus_di.notna() & minus_di.notna() & atr_pctl.notna()
    conditions = [
        ~known,
        atr_pctl >= atr_percentil_alta_vol,
        (adx >= adx_tendencia) & (plus_di > minus_di),
        adx >= adx_tendencia,
        adx <= adx_lateral,
    ]
    choices = ["indefinido", "alta_volatilidade", "tendencia_alta", "tendencia_baixa", "lateral"]
    return pd.Series(np.select(conditions, choices, default="transicao"), index=adx.index, dtype="object")

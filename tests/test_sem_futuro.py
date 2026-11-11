"""Auditoria de look-ahead: nenhum valor calculado num instante pode mudar quando o futuro muda.

Método: calcula tudo com o histórico inteiro e com o histórico cortado num ponto; nas linhas
anteriores ao corte, os valores têm de ser idênticos. Se algum indicador "olhasse o futuro",
o valor perto do corte mudaria. Também altera os candles DEPOIS do corte (em vez de só cortar),
porque um vazamento pode usar o valor futuro sem depender do tamanho da tabela.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cryptoagent.config import DEMO_RELATIVE, load_config
from cryptoagent.indicators.compute import compute_indicators

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "exemplos" / "historico" / "ETHBRL"
CFG = load_config(ROOT / "config" / "config.yaml", ROOT / DEMO_RELATIVE)


def indicators(df: pd.DataFrame) -> pd.DataFrame:
    return compute_indicators(df, CFG.indicadores, CFG.regime, tz_name=CFG.fuso.local, day_start=CFG.fuso.inicio_dia_local)


def assert_same_past(full: pd.DataFrame, part: pd.DataFrame, rows: int) -> None:
    """As `rows` primeiras linhas das duas tabelas são idênticas em todas as colunas (NaN = NaN)."""
    a, b = full.iloc[:rows].reset_index(drop=True), part.iloc[:rows].reset_index(drop=True)
    assert list(a.columns) == list(b.columns)
    for col in a.columns:
        x, y = a[col], b[col]
        if pd.api.types.is_float_dtype(x):
            same = np.isclose(x.to_numpy(), y.to_numpy(), rtol=0, atol=0, equal_nan=True)
        else:
            same = (x.astype(str) == y.astype(str)).to_numpy()
        assert same.all(), f"coluna {col} mudou no passado (primeira linha diferente: {int(np.argmin(same))})"


@pytest.mark.parametrize("tf", ["5m", "15m", "1h", "4h"])
def test_indicadores_nao_mudam_quando_o_futuro_e_cortado(tf):
    df = pd.read_parquet(SAMPLE / f"{tf}.parquet")
    full = indicators(df)
    for frac in (0.35, 0.6, 0.85):
        cut = int(len(df) * frac)
        assert_same_past(full, indicators(df.iloc[:cut]), cut)


@pytest.mark.parametrize("tf", ["5m", "15m"])
def test_indicadores_nao_mudam_quando_o_futuro_e_alterado(tf):
    df = pd.read_parquet(SAMPLE / f"{tf}.parquet")
    full = indicators(df)
    cut = int(len(df) * 0.5)
    alterado = df.copy()
    rng = np.random.default_rng(1)
    choque = 1 + rng.normal(0, 0.02, len(df) - cut)  # futuro diferente: preços com choque de ~2%
    for col in ("open", "high", "low", "close"):
        alterado.loc[cut:, col] = df.loc[cut:, col].to_numpy() * choque
    alterado.loc[cut:, "high"] = alterado.loc[cut:, ["open", "high", "low", "close"]].max(axis=1)
    alterado.loc[cut:, "low"] = alterado.loc[cut:, ["open", "high", "low", "close"]].min(axis=1)
    alterado.loc[cut:, "volume"] = df.loc[cut:, "volume"].to_numpy()[::-1]
    assert_same_past(full, indicators(alterado), cut)

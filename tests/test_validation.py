"""Validação dos candles: nulos, duplicados, ordem, grade de horário, coerência, outliers e lacunas."""

import numpy as np
import pandas as pd
import pytest

from cryptoagent.data.health import check_quality
from cryptoagent.data.models import KLINE_COLUMNS
from cryptoagent.data.validation import clean_klines, find_outliers, inspect_klines

T0 = 1_788_220_800_000  # múltiplo de 1 minuto e de 5 minutos
STEP = 60_000


def frame(n=50, step=STEP, start=T0, seed=7):
    """Candles sintéticos coerentes, com um passeio aleatório suave no preço."""
    rng = np.random.default_rng(seed)
    close = 1000 * np.exp(np.cumsum(rng.normal(0, 0.0005, n)))
    open_ = np.concatenate([[1000.0], close[:-1]])[:n]
    t = start + np.arange(n) * step
    return pd.DataFrame(
        {
            "open_time": t,
            "open": open_,
            "high": np.maximum(open_, close) * 1.0002,
            "low": np.minimum(open_, close) * 0.9998,
            "close": close,
            "volume": 10.0,
            "close_time": t + step - 1,
            "quote_volume": 10_000.0,
            "trades": 5,
            "taker_buy_base": 6.0,
            "taker_buy_quote": 6000.0,
        }
    )[KLINE_COLUMNS]


def test_serie_boa_passa_sem_alterar_nada():
    df = frame()
    clean, rep = clean_klines(df, "1m")
    assert rep.ok and rep.problems() == [] and rep.summary() == "sem problemas"
    assert len(clean) == len(df) and (clean["open_time"].to_numpy() == df["open_time"].to_numpy()).all()


def test_nulos_sao_contados_e_removidos():
    df = frame()
    df.loc[3, "close"] = np.nan
    df.loc[9, "volume"] = np.inf
    clean, rep = clean_klines(df, "1m")
    assert rep.nulls == 2 and rep.invalid_rows == 2 and not rep.ok
    assert len(clean) == 48 and clean.notna().all().all()
    assert [(g.start_ms, g.missing) for g in rep.gaps] == [(T0 + 3 * STEP, 1), (T0 + 9 * STEP, 1)]


def test_duplicados_e_fora_de_ordem_sao_corrigidos():
    df = frame(10)
    baguncado = pd.concat([df.iloc[[5, 6, 7, 8, 9]], df.iloc[[0, 1, 2, 3, 4]], df.iloc[[2]]], ignore_index=True)
    clean, rep = clean_klines(baguncado, "1m")
    assert rep.duplicates == 1 and rep.out_of_order == 2 and rep.ok  # corrigíveis: não invalidam
    assert clean["open_time"].is_monotonic_increasing and clean["open_time"].is_unique and len(clean) == 10


def test_horario_fora_da_grade_e_fechamento_incoerente():
    df = frame(10)
    df.loc[4, "open_time"] = T0 + 4 * STEP + 7_000  # começa 7 s atrasado
    df.loc[4, "close_time"] = T0 + 5 * STEP + 6_999
    df.loc[6, "close_time"] = T0 + 6 * STEP + 30_000  # fecha no meio do candle
    clean, rep = clean_klines(df, "1m")
    assert rep.misaligned == 2 and len(clean) == 8


def test_horarios_em_microssegundos_sao_aceitos():
    df = frame(5)
    df["open_time"] *= 1000
    df["close_time"] = df["close_time"] * 1000 + 999
    clean, rep = clean_klines(df, "1m")
    assert rep.ok and int(clean["open_time"].iloc[0]) == T0


@pytest.mark.parametrize(
    "coluna, valor",
    [("high", 1.0), ("low", 99_999.0), ("close", 0.0), ("open", -5.0)],
)
def test_candle_incoerente_e_removido(coluna, valor):
    df = frame(10)
    df.loc[5, coluna] = valor
    clean, rep = clean_klines(df, "1m")
    assert rep.bad_ohlc == 1 and not rep.ok and len(clean) == 9


def test_volume_negativo_e_candle_do_futuro():
    df = frame(10)
    df.loc[2, "volume"] = -1.0
    now = T0 + 7 * STEP + 1  # os candles 8 e 9 abrem depois de "agora"
    clean, rep = clean_klines(df, "1m", now_ms=now)
    assert rep.negative_volume == 1 and rep.future == 2 and rep.invalid_rows == 3 and len(clean) == 7


def test_outlier_e_relatado_mas_nao_removido():
    df = frame(500)
    df.loc[300:, ["open", "high", "low", "close"]] *= 1.25  # salto de 25% num candle
    df.loc[300, "open"] = df.loc[299, "close"]
    df.loc[300, "low"] = df.loc[299, "close"] * 0.9998
    clean, rep = clean_klines(df, "1m", outlier_sigmas=30)
    assert rep.outliers == [T0 + 300 * STEP] and rep.ok and len(clean) == 500
    assert find_outliers(frame(500), "1m", 30) == []


def test_outlier_nao_atravessa_lacuna():
    df = frame(500)
    df.loc[300:, ["open", "high", "low", "close"]] *= 1.25
    sem_meio = df.drop(index=range(290, 301)).reset_index(drop=True)  # o salto fica escondido dentro de uma lacuna
    rep = inspect_klines(sem_meio, "1m", outlier_sigmas=30)
    assert rep.outliers == [] and rep.missing_candles == 11


def test_serie_parada_nao_gera_outlier_falso():
    df = frame(200)
    df[["open", "high", "low", "close"]] = 5.0  # par sem variação: escala zero
    assert inspect_klines(df, "1m").outliers == []


def test_tabela_vazia():
    clean, rep = clean_klines(frame(0), "1m")
    assert clean.empty and rep.ok and rep.rows == 0


def test_check_de_saude_reprova_candle_invalido_e_aceita_serie_boa():
    df = frame(20)
    assert check_quality("qualidade 1m", inspect_klines(df, "1m")).ok
    df.loc[5, "high"] = 1.0
    c = check_quality("qualidade 1m", inspect_klines(df, "1m"))
    assert not c.ok and c.critical and "preço incoerente" in c.detail

"""Fuso horário: candles em UTC, dia de operação em Brasília (com o horário de verão histórico)."""

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from cryptoagent.timeutils import (
    UTC,
    datetime_to_ms,
    floor_to_interval,
    interval_ms,
    local_hour_weekday_series,
    local_trading_day,
    local_trading_day_series,
    normalize_epoch_ms,
    normalize_epoch_ms_array,
    trading_day_bounds,
    trading_day_start_ms,
)

SP = "America/Sao_Paulo"


def ms(y, mo, d, h=0, mi=0):
    return datetime_to_ms(datetime(y, mo, d, h, mi, tzinfo=UTC))


def test_dia_de_hoje_comeca_as_03h_utc():
    # Sem horário de verão (desde 2019): 00:00 em Brasília = 03:00 UTC
    assert trading_day_start_ms(date(2026, 10, 22), SP) == ms(2026, 10, 22, 3)


def test_0259_utc_ainda_e_o_dia_anterior_em_brasilia():
    assert local_trading_day(ms(2026, 10, 22, 2, 59), SP) == date(2026, 10, 21)
    assert local_trading_day(ms(2026, 10, 22, 3, 0), SP) == date(2026, 10, 22)


def test_candle_diario_da_binance_comeca_as_21h_do_dia_anterior_em_brasilia():
    # O candle 1d de 22/10 abre 00:00 UTC = 21:00 de 21/10 em Brasília
    assert local_trading_day(ms(2026, 10, 22, 0, 0), SP) == date(2026, 10, 21)


def test_horario_de_verao_historico_usa_utc_menos_2():
    # Em janeiro de 2018 o Brasil estava em horário de verão (UTC-2)
    assert trading_day_start_ms(date(2018, 1, 15), SP) == ms(2018, 1, 15, 2)


def test_meia_noite_inexistente_no_inicio_do_horario_de_verao():
    # Em 04/11/2018 o relógio pulou de 00:00 para 01:00: o dia começou às 03:00 UTC
    assert trading_day_start_ms(date(2018, 11, 4), SP) == ms(2018, 11, 4, 3)
    assert local_trading_day(ms(2018, 11, 4, 2, 59), SP) == date(2018, 11, 3)
    assert local_trading_day(ms(2018, 11, 4, 3, 0), SP) == date(2018, 11, 4)


def test_limites_do_dia_tem_24h_fora_de_mudanca_de_horario():
    start, end = trading_day_bounds(ms(2026, 10, 22, 15), SP)
    assert (start, end) == (ms(2026, 10, 22, 3), ms(2026, 10, 23, 3))


def test_inicio_de_dia_configuravel():
    # Com início às 21:00 local, o "dia" coincide com o dia UTC (como o candle 1d)
    assert local_trading_day(ms(2026, 10, 22, 23, 30), SP, "21:00") == date(2026, 10, 21)
    assert local_trading_day(ms(2026, 10, 23, 0, 30), SP, "21:00") == date(2026, 10, 22)


def test_versao_vetorizada_bate_com_a_escalar_inclusive_em_mudancas_de_horario():
    ts = pd.Series(np.arange(ms(2018, 10, 30), ms(2019, 2, 20), 37 * 60_000, dtype="int64"))
    for day_start in ("00:00", "21:00"):
        vec = local_trading_day_series(ts, SP, day_start)
        scalar = [local_trading_day(int(t), SP, day_start) for t in ts]
        assert list(vec) == scalar


def test_hora_e_dia_da_semana_locais():
    df = local_hour_weekday_series(pd.Series([ms(2026, 10, 22, 3, 0)]), SP)  # quinta 00:00 local
    assert int(df["hour"].iloc[0]) == 0
    assert int(df["weekday"].iloc[0]) == 3


@pytest.mark.parametrize("value", [1_700_000_000, 1_700_000_000_000, 1_700_000_000_000_000, 1_700_000_000_000_000_000])
def test_normaliza_segundos_ms_us_ns(value):
    assert normalize_epoch_ms(value) == 1_700_000_000_000


def test_normaliza_array():
    arr = np.array([1_700_000_000_000, 1_700_000_000_000_000], dtype="int64")
    assert list(normalize_epoch_ms_array(arr)) == [1_700_000_000_000, 1_700_000_000_000]


def test_intervalos():
    assert interval_ms("15m") == 900_000
    assert floor_to_interval(ms(2026, 10, 22, 3, 7), "5m") == ms(2026, 10, 22, 3, 5)
    assert floor_to_interval(ms(2026, 10, 22, 3, 7), "4h") == ms(2026, 10, 22, 0, 0)
    with pytest.raises(ValueError):
        interval_ms("7m")


def test_datetime_sem_fuso_e_recusado():
    with pytest.raises(ValueError):
        datetime_to_ms(datetime(2026, 1, 1))

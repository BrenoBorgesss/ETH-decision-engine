"""Tempo e fuso horário.

Regras do projeto:
- Tudo é guardado em UTC, em milissegundos desde 1970 (o mesmo formato da Binance).
- O "dia de operação" segue o horário local (padrão: America/Sao_Paulo) e começa em
  `inicio_dia_local` (padrão 00:00). O VWAP do dia e o limite de perda recomeçam aí.
- O candle diário (1d) da Binance vai de 00:00 a 00:00 UTC, ou seja, de 21:00 a 21:00
  em Brasília. Por isso o candle 1d NUNCA é usado como "o dia de hoje".
- Usamos o banco de fusos (zoneinfo + pacote tzdata), que conhece o horário de verão
  que o Brasil teve até 2019. Nada de "UTC-3 fixo".
"""

from __future__ import annotations

import time as _time
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from cryptoagent.intervals import INTERVAL_MS

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def interval_ms(interval: str) -> int:
    """Duração do intervalo em milissegundos. Levanta ValueError para intervalo desconhecido."""
    try:
        return INTERVAL_MS[interval]
    except KeyError:
        raise ValueError(f"Intervalo desconhecido: {interval!r}") from None


def now_ms() -> int:
    """Agora em milissegundos desde 1970 (UTC), pelo relógio local."""
    return _time.time_ns() // 1_000_000


def normalize_epoch_ms(value: int | float) -> int:
    """Converte um horário em segundos, ms, µs ou ns para ms.

    Desde 2025 os arquivos de spot do data.binance.vision vêm em microssegundos,
    enquanto a API REST continua em milissegundos.
    """
    v = int(value)
    a = abs(v)
    if a < 100_000_000_000:  # segundos (válido até o ano ~5138)
        return v * 1000
    if a < 100_000_000_000_000:  # milissegundos
        return v
    if a < 100_000_000_000_000_000:  # microssegundos
        return v // 1000
    return v // 1_000_000  # nanossegundos


def normalize_epoch_ms_array(values: np.ndarray | pd.Series) -> np.ndarray:
    """Versão vetorizada de `normalize_epoch_ms` (para milhares de candles de uma vez)."""
    a = np.asarray(values, dtype="int64")
    absa = np.abs(a)
    return np.select(
        [absa < 100_000_000_000, absa < 100_000_000_000_000, absa < 100_000_000_000_000_000],
        [a * 1000, a, a // 1000],
        default=a // 1_000_000,
    )


def ms_to_utc(ms: int) -> datetime:
    """Converte milissegundos desde 1970 em datetime UTC."""
    return _EPOCH + timedelta(milliseconds=int(ms))


def datetime_to_ms(dt: datetime) -> int:
    """Converte um datetime com fuso em milissegundos desde 1970."""
    if dt.tzinfo is None:
        raise ValueError("datetime sem fuso horário: use um datetime com tzinfo")
    return (dt - _EPOCH) // timedelta(milliseconds=1)


@lru_cache(maxsize=16)
def zone(name: str) -> ZoneInfo:
    """Fuso horário pelo nome (ex.: America/Sao_Paulo)."""
    return ZoneInfo(name)


def parse_hhmm(value: str) -> time:
    """Converte "HH:MM" em horário. Levanta ValueError se o formato estiver errado."""
    try:
        hh, mm = value.split(":")
        return time(int(hh), int(mm))
    except (ValueError, AttributeError):
        raise ValueError(f"Horário inválido: {value!r} (use HH:MM, ex.: 00:00)") from None


def ms_to_local(ms: int, tz_name: str) -> datetime:
    """Converte milissegundos desde 1970 em datetime no fuso dado."""
    return ms_to_utc(ms).astimezone(zone(tz_name))


def local_trading_day(ts_ms: int, tz_name: str = "America/Sao_Paulo", day_start: str = "00:00") -> date:
    """Dia de operação (no fuso local) ao qual o instante `ts_ms` pertence."""
    local = ms_to_local(ts_ms, tz_name)
    d = local.date()
    if local.time().replace(tzinfo=None) < parse_hhmm(day_start):
        d -= timedelta(days=1)
    return d


def trading_day_start_ms(day: date, tz_name: str = "America/Sao_Paulo", day_start: str = "00:00") -> int:
    """Instante UTC (ms) em que o dia de operação `day` começa.

    Se o horário de início não existiu naquele dia (ex.: início do horário de verão
    à meia-noite em 2018-11-04), o zoneinfo usa o deslocamento anterior à mudança,
    o que resulta exatamente no primeiro instante real daquele dia.
    """
    dt = datetime.combine(day, parse_hhmm(day_start), tzinfo=zone(tz_name))
    return datetime_to_ms(dt)


def trading_day_bounds(ts_ms: int, tz_name: str = "America/Sao_Paulo", day_start: str = "00:00") -> tuple[int, int]:
    """(início, fim) em ms UTC do dia de operação que contém `ts_ms`. O fim é exclusivo."""
    day = local_trading_day(ts_ms, tz_name, day_start)
    return (
        trading_day_start_ms(day, tz_name, day_start),
        trading_day_start_ms(day + timedelta(days=1), tz_name, day_start),
    )


def local_trading_day_series(
    ts_ms: pd.Series, tz_name: str = "America/Sao_Paulo", day_start: str = "00:00"
) -> pd.Series:
    """Versão vetorizada de `local_trading_day` para uma coluna de horários em ms UTC."""
    start = parse_hhmm(day_start)
    local_wall = pd.to_datetime(ts_ms, unit="ms", utc=True).dt.tz_convert(tz_name).dt.tz_localize(None)
    shifted = local_wall - pd.Timedelta(hours=start.hour, minutes=start.minute)
    return shifted.dt.date


def local_hour_weekday_series(ts_ms: pd.Series, tz_name: str = "America/Sao_Paulo") -> pd.DataFrame:
    """Hora (0-23) e dia da semana (0=segunda ... 6=domingo) no fuso local."""
    local = pd.to_datetime(ts_ms, unit="ms", utc=True).dt.tz_convert(tz_name)
    return pd.DataFrame({"hour": local.dt.hour, "weekday": local.dt.day_of_week}, index=ts_ms.index)


def floor_to_interval(ts_ms: int, interval: str) -> int:
    """Início do candle (alinhado em UTC, como na Binance) que contém `ts_ms`."""
    step = interval_ms(interval)
    return ts_ms - (ts_ms % step)


def format_local(ms: int | None, tz_name: str = "America/Sao_Paulo", fmt: str = "%d/%m %H:%M:%S") -> str:
    """Formata o instante no fuso dado; "-" para None."""
    if ms is None:
        return "-"
    return ms_to_local(ms, tz_name).strftime(fmt)

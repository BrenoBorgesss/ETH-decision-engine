"""Validação da qualidade dos candles antes de usá-los.

O que é verificado em cada tabela de candles:

- valores ausentes (nulos, NaN, infinito);
- horários duplicados e fora de ordem;
- horários fora da grade do intervalo (ex.: candle de 5m que não começa num múltiplo de 5 min)
  ou com fechamento incoerente com a abertura;
- candle incoerente: preço zero ou negativo, máxima menor que a mínima, abertura ou fechamento
  fora da faixa mínima–máxima, volume negativo;
- candle "do futuro" (abre depois do horário atual);
- outliers: retorno muito distante do normal da própria série;
- lacunas: candles que faltam na sequência.

Linhas INVÁLIDAS (ausentes, fora da grade, incoerentes, do futuro) são removidas por
`clean_klines`. Duplicados e ordem são corrigidos (fica a última versão de cada horário).
Outliers e lacunas são apenas RELATADOS: em cripto um movimento extremo pode ser real, e
inventar candles para tapar lacuna distorceria os indicadores.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from cryptoagent.data.models import KLINE_COLUMNS, Gap, empty_klines, find_gaps, normalize_klines
from cryptoagent.timeutils import interval_ms, normalize_epoch_ms_array

# Escala robusta dos retornos: percentil 90 dos desvios absolutos ÷ 1,645 (numa distribuição normal isso
# dá o desvio padrão). O desvio absoluto MEDIANO não serve aqui: em pares de pouca variação (USDTBRL)
# quase metade dos retornos de 1m é zero, a mediana dos desvios fica perto de zero e tudo vira "outlier".
SCALE_QUANTILE = 0.90
SCALE_QUANTILE_Z = 1.6449
DEFAULT_OUTLIER_SIGMAS = 30.0
PRICE_COLUMNS = ["open", "high", "low", "close"]
VOLUME_COLUMNS = ["volume", "quote_volume", "taker_buy_base", "taker_buy_quote", "trades"]


@dataclass
class QualityReport:
    """Resultado da validação de uma tabela de candles (contagens por tipo de problema)."""

    interval: str
    rows: int = 0
    nulls: int = 0  # linhas com algum valor ausente ou não finito
    duplicates: int = 0  # linhas com horário de abertura repetido
    out_of_order: int = 0  # linhas com horário menor que o da linha anterior
    misaligned: int = 0  # horário fora da grade do intervalo ou fechamento incoerente
    bad_ohlc: int = 0  # preço <= 0, máxima < mínima, abertura/fechamento fora da faixa
    negative_volume: int = 0
    future: int = 0  # candle que abre depois de agora
    invalid_rows: int = 0  # linhas com pelo menos um dos problemas acima que tornam o candle inválido
    outliers: list[int] = field(default_factory=list)  # open_time (ms) dos candles com retorno extremo
    gaps: list[Gap] = field(default_factory=list)

    @property
    def missing_candles(self) -> int:
        """Total de candles faltando, somando todas as lacunas."""
        return sum(g.missing for g in self.gaps)

    @property
    def ok(self) -> bool:
        """True se não há linha inválida (duplicados, ordem, outliers e lacunas não contam aqui)."""
        return self.invalid_rows == 0

    def problems(self) -> list[str]:
        """Um texto por tipo de problema encontrado (lista vazia = nada a relatar)."""
        items = [
            (self.nulls, "com valor ausente"),
            (self.misaligned, "com horário fora da grade"),
            (self.bad_ohlc, "com preço incoerente"),
            (self.negative_volume, "com volume negativo"),
            (self.future, "com horário no futuro"),
            (self.duplicates, "duplicado(s)"),
            (self.out_of_order, "fora de ordem"),
            (len(self.outliers), "com retorno extremo (outlier)"),
        ]
        out = [f"{n} candle(s) {text}" for n, text in items if n]
        if self.gaps:
            out.append(f"{self.missing_candles} candle(s) faltando em {len(self.gaps)} lacuna(s)")
        return out

    def summary(self) -> str:
        """Resumo em uma linha."""
        return "; ".join(self.problems()) or "sem problemas"


def _invalid_masks(df: pd.DataFrame, interval: str, now_ms: int | None) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """Máscaras por tipo de problema e os horários de abertura em ms (0 onde o horário é nulo)."""
    num = df.loc[:, KLINE_COLUMNS].apply(pd.to_numeric, errors="coerce")
    values = num.to_numpy(dtype="float64")
    nulls = ~np.isfinite(values).all(axis=1)
    safe = num.fillna(0.0).replace([np.inf, -np.inf], 0.0)
    open_ms = normalize_epoch_ms_array(safe["open_time"].to_numpy(dtype="float64").astype("int64"))
    close_ms = normalize_epoch_ms_array(safe["close_time"].to_numpy(dtype="float64").astype("int64"))
    step = interval_ms(interval)
    o, h, lo, c = (safe[k].to_numpy(dtype="float64") for k in PRICE_COLUMNS)
    valid = ~nulls
    masks = {
        "nulls": nulls,
        "misaligned": valid & ((open_ms % step != 0) | (close_ms != open_ms + step - 1)),
        "bad_ohlc": valid
        & ((np.minimum.reduce([o, h, lo, c]) <= 0) | (h < lo) | (h < np.maximum(o, c)) | (lo > np.minimum(o, c))),
        "negative_volume": valid & (safe[VOLUME_COLUMNS].to_numpy(dtype="float64") < 0).any(axis=1),
        "future": valid & (open_ms > now_ms) if now_ms is not None else np.zeros(len(df), dtype=bool),
    }
    return masks, open_ms


def find_outliers(df: pd.DataFrame, interval: str, sigmas: float = DEFAULT_OUTLIER_SIGMAS) -> list[int]:
    """Horário de abertura dos candles cujo retorno está a mais de `sigmas` desvios robustos da mediana.

    `df` precisa estar limpo e ordenado. O retorno é o logaritmo de fechamento ÷ fechamento
    anterior, só entre candles vizinhos (não atravessa lacunas). O desvio é robusto (mediana e
    percentil 90 dos desvios absolutos), para não ser puxado pelos próprios extremos.
    """
    if len(df) < 3:
        return []
    times = df["open_time"].to_numpy(dtype="int64")
    close = df["close"].to_numpy(dtype="float64")
    neighbours = np.diff(times) == interval_ms(interval)
    ret = np.log(close[1:] / close[:-1])
    used = ret[neighbours]
    if used.size < 2:
        return []
    median = float(np.median(used))
    sigma = float(np.quantile(np.abs(used - median), SCALE_QUANTILE)) / SCALE_QUANTILE_Z
    if sigma <= 0:
        return []
    extreme = neighbours & (np.abs(ret - median) > sigmas * sigma)
    return [int(t) for t in times[1:][extreme]]


def clean_klines(
    df: pd.DataFrame, interval: str, *, now_ms: int | None = None, outlier_sigmas: float = DEFAULT_OUTLIER_SIGMAS
) -> tuple[pd.DataFrame, QualityReport]:
    """Valida uma tabela de candles e devolve (tabela limpa, relatório).

    Aceita a tabela crua (horários em ms ou µs, duplicados, fora de ordem, com nulos). A tabela
    devolvida não tem linhas inválidas, está sem duplicatas e em ordem de horário.
    """
    report = QualityReport(interval, rows=len(df))
    if df.empty:
        return empty_klines(), report
    masks, open_ms = _invalid_masks(df, interval, now_ms)
    for name, mask in masks.items():
        setattr(report, name, int(mask.sum()))
    invalid = np.logical_or.reduce(list(masks.values()))
    report.invalid_rows = int(invalid.sum())
    good_times = open_ms[~invalid]
    report.duplicates = int(len(good_times) - len(np.unique(good_times)))
    report.out_of_order = int((np.diff(good_times) < 0).sum()) if len(good_times) > 1 else 0
    clean = normalize_klines(df.loc[~invalid, KLINE_COLUMNS]) if (~invalid).any() else empty_klines()
    report.outliers = find_outliers(clean, interval, outlier_sigmas)
    report.gaps = find_gaps(clean, interval)
    return clean, report


def inspect_klines(
    df: pd.DataFrame, interval: str, *, now_ms: int | None = None, outlier_sigmas: float = DEFAULT_OUTLIER_SIGMAS
) -> QualityReport:
    """Só o relatório de `clean_klines`, sem devolver a tabela."""
    return clean_klines(df, interval, now_ms=now_ms, outlier_sigmas=outlier_sigmas)[1]

"""Hoje x histórico: o movimento agora está acima, dentro ou abaixo do normal para esta hora?

E o multiplicador de confiança que a análise por horário entrega ao motor de decisão.
Só padrões VALIDADOS mexem na confiança; o resto vale 1,0 (sem efeito).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from cryptoagent.config import Sazonalidade

DAY_MS = 86_400_000


def percentile_of(value: float, sample: np.ndarray | pd.Series) -> float:
    """Percentil (0 a 100) de `value` dentro da amostra; empates contam pela metade."""
    arr = np.asarray(sample, dtype=float)
    arr = arr[~np.isnan(arr)]
    if arr.size == 0 or np.isnan(value):
        return float("nan")
    return float(((arr < value).mean() + 0.5 * (arr == value).mean()) * 100)


def activity_label(pct: float, low: float, high: float) -> str:
    """Rótulo do percentil: abaixo, dentro ou acima do normal (ou sem histórico)."""
    if np.isnan(pct):
        return "sem histórico"
    if pct < low:
        return "abaixo do normal"
    if pct > high:
        return "acima do normal"
    return "dentro do normal"


@dataclass(frozen=True)
class Comparison:
    """Comparação de um valor de agora com o histórico: valor, percentil, rótulo e tamanho da amostra."""

    name: str
    value_pct: float
    percentile: float
    label: str
    sample_size: int


def candle_vs_same_hour(df_b: pd.DataFrame, days: int, low: float, high: float) -> Comparison:
    """Amplitude do ÚLTIMO candle x candles da mesma hora (Brasília) nos últimos `days` dias.

    Usa só o passado: o próprio candle não entra na amostra.
    """
    last = df_b.iloc[-1]
    since = int(last["open_time"]) - days * DAY_MS
    hist = df_b[(df_b["hour"] == last["hour"]) & (df_b["open_time"] >= since) & (df_b["open_time"] < last["open_time"])]
    pct = percentile_of(float(last["range_pct"]), hist["range_pct"])
    return Comparison(
        f"último candle ({int(last['hour']):02d}h)",
        float(last["range_pct"]),
        pct,
        activity_label(pct, low, high),
        len(hist),
    )


def day_range_vs_history(df_b: pd.DataFrame, trading_day: pd.Series, days: int, low: float, high: float) -> Comparison:
    """Variação do dia ATÉ AGORA x a variação dos dias anteriores até o mesmo horário.

    Variação do dia = (máxima − mínima desde 00:00 de Brasília) / abertura do dia × 100.
    "Mesmo horário" = mesmo número de candles desde o início do dia.
    """
    d = pd.DataFrame(
        {
            "day": trading_day.to_numpy(),
            "open": df_b["open"].to_numpy(),
            "high": df_b["high"].to_numpy(),
            "low": df_b["low"].to_numpy(),
        }
    )
    g = d.groupby("day", sort=False)
    d["slot"] = g.cumcount()
    day_open = g["open"].transform("first")
    d["range_so_far"] = (g["high"].cummax() - g["low"].cummin()) / day_open * 100
    today, slot = d["day"].iloc[-1], int(d["slot"].iloc[-1])
    previous_days = [x for x in pd.unique(d["day"]) if x != today][-days:]
    hist = d[d["day"].isin(previous_days) & (d["slot"] == slot)]["range_so_far"]
    value = float(d["range_so_far"].iloc[-1])
    pct = percentile_of(value, hist)
    return Comparison("variação do dia até agora", value, pct, activity_label(pct, low, high), len(hist))


@dataclass
class ContextFactor:
    """Fator que multiplica a confiança por causa do horário, com os motivos."""

    factor: float
    reasons: list[str] = field(default_factory=list)


def context_multiplier(
    hour_class: str | None, hour_pattern_validated: bool, activity: str | None, s: Sazonalidade
) -> ContextFactor:
    """Multiplicador de confiança da análise por horário (sempre <= 1: só reduz, nunca cria sinal)."""
    ctx = ContextFactor(1.0)
    label = {"compensa": "compensa", "neutro": "neutro", "nao_compensa": "não compensa"}.get(
        hour_class or "", hour_class
    )

    def fmt(x: float) -> str:
        return f"{x:g}".replace(".", ",")

    if hour_class and hour_pattern_validated:
        mult = {"compensa": s.mult_compensa, "neutro": s.mult_neutro, "nao_compensa": s.mult_nao_compensa}[hour_class]
        ctx.factor *= mult
        ctx.reasons.append(f"horário que historicamente '{label}' as taxas (padrão validado): × {fmt(mult)}")
    elif hour_class:
        ctx.reasons.append(f"horário '{label}', mas o padrão NÃO passou na validação: sem efeito (× 1)")
    if activity == "abaixo do normal":
        ctx.factor *= s.mult_parado_agora
        ctx.reasons.append(f"movimento agora abaixo do normal para esta hora: × {fmt(s.mult_parado_agora)}")
    return ctx

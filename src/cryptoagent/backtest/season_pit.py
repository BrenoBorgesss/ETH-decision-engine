"""Análise por horário "no ponto do tempo" para o backtest.

No modo ao vivo, o fator do horário vem do relatório mais recente. No backtest isso seria olhar
o futuro. Aqui, para cada mês M, os padrões são aprendidos e validados SÓ com dados antes de M,
e o "movimento agora" de cada candle é comparado só com os 90 dias anteriores a ele.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from cryptoagent.config import Sazonalidade
from cryptoagent.seasonality.compare import activity_label, context_multiplier, percentile_of
from cryptoagent.seasonality.profiles import add_buckets, bucket_stats, classify
from cryptoagent.seasonality.validation import validate

DAY_MS = 86_400_000


def same_hour_percentiles(b: pd.DataFrame, days: int) -> np.ndarray:
    """Percentil da amplitude de cada candle x candles da mesma hora nos `days` dias anteriores."""
    out = np.full(len(b), np.nan)
    ranges = b["range_pct"].to_numpy()
    times = b["open_time"].to_numpy()
    for _, idx in b.groupby("hour").indices.items():
        t, v = times[idx], ranges[idx]
        for j in range(len(idx)):
            start = int(np.searchsorted(t, t[j] - days * DAY_MS, side="left"))
            out[idx[j]] = percentile_of(v[j], v[start:j])
    return out


@dataclass
class SeasonPIT:
    """Análise por horário "ponto no tempo": para cada mês, o perfil que se conhecia até ali, para o backtest não usar informação do futuro."""
    month_starts: np.ndarray  # ms
    hour_info: list[dict[int, tuple[str, bool]]]  # por mês: hora -> (classe, padrão validado até ali)
    candle_close: np.ndarray  # close_time dos candles do perfil
    candle_hour: np.ndarray
    candle_activity: list[str]
    s: Sazonalidade

    @classmethod
    def build(cls, trade_df: pd.DataFrame, cost_pct: float, s: Sazonalidade, tz: str, atr_n: int) -> SeasonPIT:
        """Constrói os perfis mês a mês, cada um só com os candles anteriores ao início do mês."""
        b = add_buckets(trade_df, tz, atr_n)
        first = pd.Timestamp(int(b["open_time"].iloc[0]), unit="ms", tz="UTC")
        last = pd.Timestamp(int(b["open_time"].iloc[-1]), unit="ms", tz="UTC")
        months = pd.date_range((first + pd.offsets.MonthBegin(1)).normalize(), last, freq="MS")
        starts, infos = [], []
        window = int(s.meses_treino * 30.44 * DAY_MS)
        for m in months:
            m_ms = int(m.value // 1_000_000)
            past = b[b["open_time"] < m_ms]
            info: dict[int, tuple[str, bool]] = {}
            if len(past) > 0 and (m_ms - int(past["open_time"].iloc[0])) >= (s.meses_treino + s.meses_teste) * 28 * DAY_MS:
                report = validate(past, cost_pct, s)
                kept = {(p.key, p.claim) for p in report.kept("hora")}
                recent = past[past["open_time"] >= m_ms - window]
                classes = classify(bucket_stats(recent, ["hour"], cost_pct, s.custo_folga), s.pct_min_compensa, s.pct_max_nao_compensa)
                info = {int(h): (c, (f"{int(h):02d}h", c) in kept) for h, c in classes.items()}
            starts.append(m_ms)
            infos.append(info)
        pct = same_hour_percentiles(b, s.dias_comparacao)
        activity = [activity_label(x, s.percentil_baixo, s.percentil_alto) for x in pct]
        return cls(np.array(starts, dtype="int64"), infos, b["close_time"].to_numpy(), b["hour"].to_numpy(), activity, s)

    def factor_at(self, t_ms: int) -> tuple[float, list[str]]:
        """Fator de confiança do horário no instante t (só com o que se sabia até t)."""
        i = int(np.searchsorted(self.candle_close, t_ms, side="left")) - 1  # último candle fechado
        if i < 0:
            return 1.0, []
        m = int(np.searchsorted(self.month_starts, t_ms, side="right")) - 1
        info = self.hour_info[m] if m >= 0 else {}
        hour = int(self.candle_hour[i])
        cls_, kept = info.get(hour, (None, False))
        ctx = context_multiplier(cls_, kept, self.candle_activity[i], self.s)
        return ctx.factor, ctx.reasons

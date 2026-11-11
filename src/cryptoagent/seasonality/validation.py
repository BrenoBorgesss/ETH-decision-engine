"""Validação walk-forward dos padrões de horário.

Como funciona:
1. Divide o histórico em janelas: aprende em N meses (treino) e confere no mês seguinte
   (teste), que não foi usado para aprender. Anda um mês e repete.
2. Em cada janela de treino, registra as AFIRMAÇÕES que os dados sugerem, por exemplo
   "às 14h compensa as taxas" ou "domingo é menos volátil".
3. Confere cada afirmação no mês de teste.
4. Um padrão só é MANTIDO se foi afirmado em pelo menos metade das janelas e se confirmou
   em >= consistencia_min_pct% das vezes. O resto é DESCARTADO, e aparece no relatório.

Também mede se o "formato" do dia se repete: correlação de Spearman entre a amplitude
mediana por hora no treino e no teste.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from cryptoagent.config import Sazonalidade
from cryptoagent.seasonality.profiles import WEEKDAYS_PT, bucket_stats, classify, volatility_deviation


@dataclass(frozen=True)
class Fold:
    """Uma janela do walk-forward: período de treino e período de teste (fins exclusivos)."""
    train_start_ms: int
    train_end_ms: int  # exclusivo
    test_start_ms: int
    test_end_ms: int  # exclusivo


@dataclass
class PatternResult:
    """Um padrão de horário e quantas vezes ele se confirmou fora da amostra."""
    kind: str  # "hora", "dia_semana", "hora_dia"
    key: str  # ex.: "14h", "dom", "dom 14h"
    claim: str  # "compensa", "nao_compensa", "mais_volatil", "menos_volatil"
    claims: int = 0  # em quantas janelas de treino a afirmação apareceu
    held: int = 0  # em quantas ela se confirmou no teste
    folds: int = 0
    kept: bool = False

    @property
    def hold_rate(self) -> float:
        """Percentual das vezes em que o padrão se confirmou no teste."""
        return self.held / self.claims * 100 if self.claims else 0.0


@dataclass
class ValidationReport:
    """Resultado da validação walk-forward dos padrões de horário."""
    folds: list[Fold]
    spearman: list[float]
    patterns: list[PatternResult] = field(default_factory=list)

    @property
    def profile_repeats(self) -> bool:
        """O formato do dia (horas mais/menos agitadas) se repete fora da amostra?"""
        if not self.spearman:
            return False
        good = sum(1 for s in self.spearman if s >= 0.5)
        return good / len(self.spearman) >= 0.75

    def kept(self, kind: str | None = None) -> list[PatternResult]:
        """Padrões mantidos (opcionalmente só de um tipo)."""
        return [p for p in self.patterns if p.kept and (kind is None or p.kind == kind)]

    def discarded(self, kind: str | None = None) -> list[PatternResult]:
        """Padrões descartados (opcionalmente só de um tipo)."""
        return [p for p in self.patterns if not p.kept and (kind is None or p.kind == kind)]


def _month_starts(first_ms: int, last_ms: int) -> list[pd.Timestamp]:
    start = pd.Timestamp(first_ms, unit="ms", tz="UTC").normalize()
    if start.day != 1 or pd.Timestamp(first_ms, unit="ms", tz="UTC") != start:
        start = (start + pd.offsets.MonthBegin(1)).normalize()
    end = pd.Timestamp(last_ms, unit="ms", tz="UTC")
    return list(pd.date_range(start, end, freq="MS"))


def make_folds(open_times: pd.Series, train_months: int, test_months: int) -> list[Fold]:
    """Janelas por mês-calendário (UTC). O teste vem sempre DEPOIS do treino, sem sobreposição."""
    months = _month_starts(int(open_times.min()), int(open_times.max()))
    def to_ms(ts: pd.Timestamp) -> int:
        return int(ts.value // 1_000_000)
    folds = []
    i = 0
    # o fim do teste (months[i + treino + teste]) precisa existir: só usa meses de teste completos
    while i + train_months + test_months <= len(months) - 1:
        tr0, tr1, te1 = months[i], months[i + train_months], months[i + train_months + test_months]
        folds.append(Fold(to_ms(tr0), to_ms(tr1), to_ms(tr1), to_ms(te1)))
        i += test_months
    return folds


def _hour_label(h: int) -> str:
    return f"{h:02d}h"


def validate(df_b: pd.DataFrame, cost_pct: float, s: Sazonalidade) -> ValidationReport:
    """`df_b` precisa ter passado por add_buckets."""
    folds = make_folds(df_b["open_time"], s.meses_treino, s.meses_teste)
    report = ValidationReport(folds, [])
    results: dict[tuple[str, str, str], PatternResult] = {}
    dev_min = s.desvio_min_volatilidade

    def record(kind: str, key: str, claim: str, held: bool) -> None:
        r = results.setdefault((kind, key, claim), PatternResult(kind, key, claim))
        r.claims += 1
        r.held += int(held)

    for fold in folds:
        tr = df_b[(df_b["open_time"] >= fold.train_start_ms) & (df_b["open_time"] < fold.train_end_ms)]
        te = df_b[(df_b["open_time"] >= fold.test_start_ms) & (df_b["open_time"] < fold.test_end_ms)]
        if tr.empty or te.empty:
            continue

        # 1) formato do dia + afirmações "esta hora compensa / não compensa as taxas"
        h_tr = bucket_stats(tr, ["hour"], cost_pct, s.custo_folga)
        h_te = bucket_stats(te, ["hour"], cost_pct, s.custo_folga)
        joined = h_tr[["range_median"]].join(h_te[["range_median"]], rsuffix="_te", how="inner")
        report.spearman.append(float(joined["range_median"].rank().corr(joined["range_median_te"].rank())))
        c_tr = classify(h_tr, s.pct_min_compensa, s.pct_max_nao_compensa)
        c_te = classify(h_te, s.pct_min_compensa, s.pct_max_nao_compensa)
        for hour, cls in c_tr.items():
            if cls != "neutro" and hour in c_te.index:
                record("hora", _hour_label(int(hour)), cls, c_te[hour] == cls)

        # 2) dia da semana e 3) hora × dia: "mais/menos volátil que o normal"
        med_tr, med_te = tr["range_pct"].median(), te["range_pct"].median()
        for kind, keys in (("dia_semana", ["weekday"]), ("hora_dia", ["weekday", "hour"])):
            d_tr = volatility_deviation(bucket_stats(tr, keys, cost_pct, s.custo_folga), med_tr)
            d_te = volatility_deviation(bucket_stats(te, keys, cost_pct, s.custo_folga), med_te)
            for key, dev in d_tr.items():
                if abs(dev) < dev_min or key not in d_te.index:
                    continue
                claim = "mais_volatil" if dev > 0 else "menos_volatil"
                test_dev = d_te[key]
                held = (test_dev > dev_min / 2) if dev > 0 else (test_dev < -dev_min / 2)
                label = WEEKDAYS_PT[int(key)] if kind == "dia_semana" else f"{WEEKDAYS_PT[int(key[0])]} {_hour_label(int(key[1]))}"
                record(kind, label, claim, bool(held))

    n_folds = len(report.spearman)
    min_claims = max(2, (n_folds + 1) // 2)
    for r in results.values():
        r.folds = n_folds
        r.kept = r.claims >= min_claims and r.hold_rate >= s.consistencia_min_pct
    report.patterns = sorted(results.values(), key=lambda p: (p.kind, not p.kept, p.key, p.claim))
    return report

"""Detecção de mudança de comportamento (drift): os dados recentes ainda se parecem com os do treino?

Usa o índice de estabilidade da população (PSI), comum em risco de crédito:
PSI = Σ (recente% − referência%) × ln(recente% ÷ referência%), em faixas definidas pela referência.
Regra de bolso: abaixo de 0,10 = estável; 0,10 a 0,25 = mudança moderada; acima de 0,25 = mudança forte.

Comparações (recente = últimos `dias_recentes_drift` dias antes do instante; referência = os meses de
treino antes disso):
1. Mercado: distribuição do regime (15m), do ATR% e do retorno absoluto por candle do timeframe de setup.
2. Momentos candidatos: as variáveis do modelo nos momentos em que a estratégia poderia comprar.
3. Simulação: a média por operação da carteira virtual x o intervalo de 95% do campeão fora da amostra.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from cryptoagent.backtest.metrics import mean_ci95

DAY_MS = 86_400_000
EPS = 1e-4
MIN_ROWS = 30


@dataclass(frozen=True)
class DriftItem:
    """Uma comparação: nome, PSI (ou outra medida), se passou do alerta e o detalhe."""
    name: str
    value: float | None
    alert: bool
    detail: str


@dataclass
class DriftReport:
    """Todas as comparações e se, no conjunto, há mudança de regime."""
    items: list[DriftItem] = field(default_factory=list)

    @property
    def drift(self) -> bool:
        """True se alguma comparação passou do alerta."""
        return any(i.alert for i in self.items)

    @property
    def summary(self) -> str:
        """Texto curto com as comparações em alerta (vazio se nenhuma)."""
        return "; ".join(f"{i.name}: {i.detail}" for i in self.items if i.alert)


def psi_numeric(reference: np.ndarray, recent: np.ndarray, bins: int = 10) -> float | None:
    """PSI de uma variável numérica, com faixas pelos quantis da referência. None se faltar amostra."""
    ref = reference[np.isfinite(reference)]
    rec = recent[np.isfinite(recent)]
    if len(ref) < MIN_ROWS or len(rec) < MIN_ROWS:
        return None
    edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return 0.0
    edges[0], edges[-1] = -np.inf, np.inf
    e = np.histogram(ref, edges)[0] / len(ref)
    a = np.histogram(rec, edges)[0] / len(rec)
    e, a = np.maximum(e, EPS), np.maximum(a, EPS)
    return float(np.sum((a - e) * np.log(a / e)))


def psi_categorical(reference: pd.Series, recent: pd.Series) -> float | None:
    """PSI de uma variável de categorias (ex.: regime). None se faltar amostra."""
    if len(reference) < MIN_ROWS or len(recent) < MIN_ROWS:
        return None
    cats = sorted(set(reference) | set(recent))
    e = reference.value_counts(normalize=True).reindex(cats, fill_value=0).to_numpy()
    a = recent.value_counts(normalize=True).reindex(cats, fill_value=0).to_numpy()
    e, a = np.maximum(e, EPS), np.maximum(a, EPS)
    return float(np.sum((a - e) * np.log(a / e)))


def _item(name: str, value: float | None, limit: float) -> DriftItem:
    if value is None:
        return DriftItem(name, None, False, "amostra insuficiente para comparar")
    level = "forte" if value > limit else "moderada" if value > 0.10 else "estável"
    return DriftItem(name, value, value > limit, f"PSI {value:.2f} ({level})")


def market_drift(setup: pd.DataFrame, until_ms: int, recent_days: int, ref_days: int, limit: float) -> list[DriftItem]:
    """Compara regime, ATR% e retorno absoluto do timeframe de setup: recentes x referência."""
    t = setup["close_time"].to_numpy()
    rec = setup[(t >= until_ms - recent_days * DAY_MS) & (t < until_ms)]
    ref = setup[(t >= until_ms - (recent_days + ref_days) * DAY_MS) & (t < until_ms - recent_days * DAY_MS)]
    ret = lambda df: np.abs(np.log(df["close"] / df["close"].shift(1))).to_numpy()  # noqa: E731 - uso local
    return [
        _item("regime do mercado", psi_categorical(ref["regime"], rec["regime"]), limit),
        _item("volatilidade (ATR%)", psi_numeric(ref["atr_pct"].to_numpy(), rec["atr_pct"].to_numpy()), limit),
        _item("tamanho dos movimentos", psi_numeric(ret(ref), ret(rec)), limit),
    ]


def candidate_drift(dataset: pd.DataFrame, features: list[str], until_ms: int, recent_days: int, ref_days: int, limit: float) -> DriftItem:
    """Maior PSI entre as variáveis dos momentos candidatos: recentes x referência."""
    t = dataset["t_ms"].to_numpy()
    rec = dataset[(t >= until_ms - recent_days * DAY_MS) & (t < until_ms)]
    ref = dataset[(t >= until_ms - (recent_days + ref_days) * DAY_MS) & (t < until_ms - recent_days * DAY_MS)]
    values = {f: psi_numeric(ref[f].to_numpy(dtype=float), rec[f].to_numpy(dtype=float)) for f in features}
    known = {f: v for f, v in values.items() if v is not None}
    if not known:
        return DriftItem("momentos candidatos", None, False, f"amostra insuficiente ({len(rec)} recentes, {len(ref)} de referência)")
    worst = max(known, key=known.get)
    v = known[worst]
    return DriftItem("momentos candidatos", v, v > limit, f"maior PSI {v:.2f} em {worst} ({len(rec)} recentes x {len(ref)} de referência)")


def simulation_drift(paper_pnl_pct: list[float], champion_ci95: list[float] | None, min_trades: int = 10) -> DriftItem:
    """A carteira virtual está rendendo abaixo do que o campeão mostrou fora da amostra?"""
    n = len(paper_pnl_pct)
    if n < min_trades or not champion_ci95 or champion_ci95[0] is None or math.isnan(champion_ci95[0]):
        return DriftItem("simulação x backtest", None, False, f"{n} operação(ões) virtual(is) fechada(s) (mínimo {min_trades} para comparar)")
    mean = float(np.mean(paper_pnl_pct))
    lo, hi = mean_ci95(paper_pnl_pct)
    alert = hi < champion_ci95[0]  # mesmo o melhor caso da simulação fica abaixo do pior caso do backtest
    return DriftItem("simulação x backtest", mean, alert,
                     f"simulação {mean:+.3f}% por operação (IC {lo:+.3f} a {hi:+.3f}) x backtest a partir de {champion_ci95[0]:+.3f}%")

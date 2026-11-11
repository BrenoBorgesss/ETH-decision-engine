"""Detecção de mudança de comportamento (drift): os dados recentes ainda se parecem com os do treino?

Usa o índice de estabilidade da população (PSI), comum em risco de crédito:
PSI = Σ (recente% − referência%) × ln(recente% ÷ referência%), em faixas definidas pela referência.
Regra de bolso: abaixo de 0,10 = estável; 0,10 a 0,25 = mudança moderada; acima de 0,25 = mudança forte.

Com amostras pequenas o PSI sai alto mesmo sem mudança nenhuma (só pelo sorteio de quais casos caíram em
cada faixa). O valor esperado nesse caso é cerca de (faixas − 1) × (1/n_recente + 1/n_referência), e ele é
DESCONTADO aqui. Ex.: 51 casos recentes contra 364 de referência, em 10 faixas, dão ~0,20 de PSI só por acaso.

Comparações (recente = últimos `dias_recentes_drift` dias antes do instante; referência = os meses de
treino antes disso):
1. Mercado: distribuição do regime (15m), do ATR% e do retorno absoluto por candle do timeframe de setup.
2. Momentos candidatos: as variáveis do modelo nos momentos em que a estratégia poderia comprar.
Só há alerta se o PSI passar do limite E a diferença for incomum para o período (ver `window_test`).
3. Simulação: a média por operação da carteira virtual x o intervalo de 95% do campeão fora da amostra.
"""

from __future__ import annotations

import math
from collections.abc import Callable
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
    return _psi(e, a, len(ref), len(rec))


def _psi(e: np.ndarray, a: np.ndarray, n_ref: int, n_rec: int) -> float:
    """PSI já descontado o valor esperado só pelo acaso com amostras deste tamanho (nunca abaixo de zero)."""
    e, a = np.maximum(e, EPS), np.maximum(a, EPS)
    raw = float(np.sum((a - e) * np.log(a / e)))
    return max(0.0, raw - (len(e) - 1) * (1 / n_ref + 1 / n_rec))


def psi_categorical(reference: pd.Series, recent: pd.Series) -> float | None:
    """PSI de uma variável de categorias (ex.: regime). None se faltar amostra."""
    if len(reference) < MIN_ROWS or len(recent) < MIN_ROWS:
        return None
    cats = sorted(set(reference) | set(recent))
    e = reference.value_counts(normalize=True).reindex(cats, fill_value=0).to_numpy()
    a = recent.value_counts(normalize=True).reindex(cats, fill_value=0).to_numpy()
    return _psi(e, a, len(reference), len(recent))


def window_test(
    times: np.ndarray, stat: Callable[[np.ndarray, np.ndarray], float | None], until_ms: int, recent_days: int, ref_days: int,
    step_days: int = 2,
) -> tuple[float | None, float | None, int]:
    """Quão incomum é a diferença do último período? (estatística observada, p, janelas comparadas)

    Preços e indicadores mudam aos poucos: casos vizinhos no tempo se parecem, e qualquer período de 30 dias
    difere um pouco do resto. Então a pergunta certa é "o último período está MAIS diferente do que um período
    qualquer costuma estar?". Para responder, a mesma estatística é calculada para cada janela de `recent_days`
    dias dentro do intervalo analisado (andando `step_days` por vez), contra o resto do intervalo. p = fração das
    janelas com diferença igual ou maior que a do último período.
    """
    start = until_ms - (recent_days + ref_days) * DAY_MS
    inside = (times >= start) & (times < until_ms)

    def at(end: int) -> float | None:
        rec = inside & (times >= end - recent_days * DAY_MS) & (times < end)
        return stat(rec, inside & ~rec)

    observed = at(until_ms)
    if observed is None:
        return None, None, 0
    values = [v for end in range(start + recent_days * DAY_MS, until_ms + 1, step_days * DAY_MS) if (v := at(end)) is not None]
    values.append(observed)
    return observed, sum(v >= observed for v in values) / len(values), len(values)


def _windowed_item(name: str, observed: float | None, p: float | None, n: int, limit: float, alpha: float, extra: str = "") -> DriftItem:
    if observed is None:
        return DriftItem(name, None, False, f"amostra insuficiente para comparar{extra}")
    alert = observed > limit and p is not None and p < alpha
    level = "forte" if observed > limit else "moderada" if observed > 0.10 else "pequena"
    unusual = "incomum" if p is not None and p < alpha else "comum"
    return DriftItem(name, observed, alert, f"PSI {observed:.2f} ({level}); diferença {unusual} no período: p = {p:.2f} em {n} janelas{extra}")


def market_drift(setup: pd.DataFrame, until_ms: int, recent_days: int, ref_days: int, limit: float, alpha: float = 0.05) -> list[DriftItem]:
    """Regime, ATR% e tamanho dos movimentos do timeframe de setup: o último período x o resto (ver `window_test`)."""
    t = setup["close_time"].to_numpy()
    regime = setup["regime"].reset_index(drop=True)
    atr = setup["atr_pct"].to_numpy(dtype=float)
    ret = np.abs(np.log(setup["close"] / setup["close"].shift(1))).to_numpy(dtype=float)
    items = []
    for name, fn in (
        ("regime do mercado", lambda r, x: psi_categorical(regime[x], regime[r])),
        ("volatilidade (ATR%)", lambda r, x: psi_numeric(atr[x], atr[r])),
        ("tamanho dos movimentos", lambda r, x: psi_numeric(ret[x], ret[r])),
    ):
        items.append(_windowed_item(name, *window_test(t, fn, until_ms, recent_days, ref_days), limit, alpha))
    return items


def candidate_drift(
    dataset: pd.DataFrame, features: list[str], until_ms: int, recent_days: int, ref_days: int, limit: float, alpha: float = 0.05,
) -> DriftItem:
    """Maior PSI entre as variáveis dos momentos candidatos: o último período x o resto (ver `window_test`)."""
    t = dataset["t_ms"].to_numpy()
    cols = {f: dataset[f].to_numpy(dtype=float) for f in features}
    worst_name = {}

    def stat(rec: np.ndarray, ref: np.ndarray) -> float | None:
        if rec.sum() < MIN_ROWS or ref.sum() < MIN_ROWS:
            return None
        vals = {f: psi_numeric(c[ref], c[rec]) for f, c in cols.items()}
        known = {f: v for f, v in vals.items() if v is not None}
        if not known:
            return None
        f = max(known, key=known.get)
        worst_name["last"] = f
        return known[f]

    rec_now = (t >= until_ms - recent_days * DAY_MS) & (t < until_ms)
    observed = stat(rec_now, (t >= until_ms - (recent_days + ref_days) * DAY_MS) & ~rec_now & (t < until_ms))
    name = worst_name.get("last", "")
    obs, p, n = window_test(t, stat, until_ms, recent_days, ref_days)
    extra = f"; maior em {name}" if observed is not None else f" ({int(rec_now.sum())} momentos recentes)"
    return _windowed_item("momentos candidatos", obs, p, n, limit, alpha, extra)


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

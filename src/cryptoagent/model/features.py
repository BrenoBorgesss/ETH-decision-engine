"""Variáveis (features) do modelo desafiador, tiradas do MESMO contexto que o motor de regras enxerga.

Tudo vem de candles já fechados no instante da decisão (`MarketView.last`), dos níveis calculados
pelas regras e da hora local. Nada aqui olha o futuro: é a mesma foto que gerou a decisão.
"""

from __future__ import annotations

import math

from cryptoagent.config import Estrategia
from cryptoagent.engine.context import DecisionContext
from cryptoagent.engine.decision import COST_GATES, Decision
from cryptoagent.timeutils import ms_to_local

REGIME_DUMMIES = ("tendencia_alta", "tendencia_baixa", "lateral", "alta_volatilidade")  # "transicao" é a referência

FEATURES: tuple[str, ...] = (
    "setup_dist_vwap_atr",
    "setup_bb_pctb",
    "setup_rsi",
    "setup_atr_pctl",
    "setup_rvol",
    "gatilho_rsi",
    "gatilho_bb_pctb",
    "gatilho_taker_sell_ratio",
    "gatilho_sell_vol_vs_peak",
    "confirmacao_adx",
    "confirmacao_di_saldo",
    "contexto_ema50_slope",
    "contexto_ema50_vs_200_pct",
    "alvo_em_custos",
    "risco_retorno",
    "risco_pct",
    "nota_sinais",
    "fator_tendencia",
    "hora_sen",
    "hora_cos",
    *(f"regime_{r}" for r in REGIME_DUMMIES),
)


def is_candidate(d: Decision) -> bool:
    """Momento em que o desafiador pode comprar: sinal técnico completo (esticado + gatilho + níveis com tamanho),
    sem trava de CUSTO (alvo perto demais, risco/retorno) e sem trava de SEGURANÇA (dados, conta, spread, perda do dia).

    As travas de estratégia (regime, filtro de tendência, confiança mínima) ficam a cargo do modelo, que as
    recebe como variáveis.
    """
    if d.position_note is not None or d.levels is None or not d.levels.size_brl:
        return False
    if d.trigger is None or not d.trigger.ok or not d.parts or d.parts[0].value <= 0:
        return False
    if any(k in d.strategy_gates for k in COST_GATES):
        return False
    gates = set(d.strategy_gates.values())
    return all(b in gates for b in d.blocks)


def _num(x: object) -> float:
    try:
        v = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("nan")
    return v


class _Row:
    """Lê colunas de uma linha devolvendo NaN para coluna ausente (o modelo trata ausente como a média do treino)."""

    def __init__(self, row: object) -> None:
        self.row = row

    def __getitem__(self, col: str) -> float:
        try:
            return _num(self.row[col])  # type: ignore[index]
        except (KeyError, IndexError):
            return float("nan")


def extract_features(ctx: DecisionContext, d: Decision, p: Estrategia, tz: str) -> dict[str, float]:
    """As variáveis de um momento candidato, num dicionário {nome: valor}. Valores ausentes viram NaN."""
    m = ctx.market
    s15, g5, h1, h4 = (_Row(m.last(tf)) for tf in (p.tf_setup, p.tf_gatilho, p.tf_confirmacao, p.tf_contexto))
    lv = d.levels
    hour = ms_to_local(m.time_ms, tz).hour + ms_to_local(m.time_ms, tz).minute / 60
    regime = str(d.regime or "")
    ema200 = _num(h4["ema_200"])
    out = {
        "setup_dist_vwap_atr": _num(s15["dist_vwap_atr"]),
        "setup_bb_pctb": _num(s15["bb_pctb"]),
        "setup_rsi": _num(s15["rsi"]),
        "setup_atr_pctl": _num(s15["atr_pctl"]),
        "setup_rvol": _num(s15["rvol"]),
        "gatilho_rsi": _num(g5["rsi"]),
        "gatilho_bb_pctb": _num(g5["bb_pctb"]),
        "gatilho_taker_sell_ratio": _num(g5["taker_sell_ratio"]),
        "gatilho_sell_vol_vs_peak": _num(g5["sell_vol_vs_peak"]),
        "confirmacao_adx": _num(h1["adx"]),
        "confirmacao_di_saldo": _num(h1["plus_di"]) - _num(h1["minus_di"]),
        "contexto_ema50_slope": _num(h4["ema_50_slope"]),
        "contexto_ema50_vs_200_pct": (_num(h4["ema_50"]) / ema200 - 1) * 100 if ema200 else float("nan"),
        "alvo_em_custos": lv.target_distance_pct / ctx.cost_pct if lv is not None and ctx.cost_pct > 0 else float("nan"),
        "risco_retorno": lv.rr if lv is not None else float("nan"),
        "risco_pct": lv.risk_net_pct if lv is not None else float("nan"),
        "nota_sinais": d.parts[0].value if d.parts else float("nan"),
        "fator_tendencia": d.parts[1].value if len(d.parts) > 1 else float("nan"),
        "hora_sen": math.sin(2 * math.pi * hour / 24),
        "hora_cos": math.cos(2 * math.pi * hour / 24),
    }
    out.update({f"regime_{r}": float(regime == r) for r in REGIME_DUMMIES})
    return out

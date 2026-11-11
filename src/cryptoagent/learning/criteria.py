"""Critérios de promoção: quando um desafiante pode substituir o campeão.

Todos precisam ser atendidos, comparando os dois no MESMO período fora da amostra, já com custos:
1. amostra mínima: operações e dias fora da amostra;
2. média por operação maior que a do campeão em pelo menos `melhoria_min_expectativa_pct`;
3. média por operação maior que `expectativa_min_pct` (padrão: lucro depois dos custos);
4. retorno total maior que o do campeão;
5. drawdown máximo não pior que o do campeão (com tolerância configurável);
6. sem mudança de regime detectada nos dados recentes (se `bloquear_promocao_com_drift`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from cryptoagent.config import Aprendizado
from cryptoagent.fmt import num, pct


@dataclass(frozen=True)
class Verdict:
    """Resultado da comparação: se promove e, critério a critério, o que passou e o que não passou."""
    promote: bool
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def _f(x: object) -> float:
    try:
        v = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return math.nan
    return v


def judge(challenger: dict, champion: dict, oos_days: float, a: Aprendizado, *, drift: bool) -> Verdict:
    """Aplica os critérios (ver a descrição do módulo) às métricas fora da amostra do desafiante e do campeão."""
    ok, bad = [], []

    def check(cond: bool, text: str) -> None:
        (ok if cond else bad).append(text)

    n = int(challenger.get("operacoes") or 0)
    check(n >= a.operacoes_min, f"operações fora da amostra: {n} (mínimo {a.operacoes_min})")
    check(oos_days >= a.dias_min, f"dias fora da amostra: {oos_days:.0f} (mínimo {a.dias_min})")
    e_ch, e_cp = _f(challenger.get("expectativa_pct")), _f(champion.get("expectativa_pct"))
    base = e_cp if not math.isnan(e_cp) else 0.0  # campeão sem operações: compara com zero
    check(not math.isnan(e_ch) and e_ch >= base + a.melhoria_min_expectativa_pct,
          f"média por operação {pct(e_ch, 3, sign=True)} x campeão {pct(e_cp, 3, sign=True)} (melhoria mínima {num(a.melhoria_min_expectativa_pct, 2)} ponto)")
    check(not math.isnan(e_ch) and e_ch > a.expectativa_min_pct,
          f"média por operação {pct(e_ch, 3, sign=True)} precisa passar de {pct(a.expectativa_min_pct, 3, sign=True)}")
    r_ch, r_cp = _f(challenger.get("retorno_total_pct")), _f(champion.get("retorno_total_pct"))
    check(r_ch > (r_cp if not math.isnan(r_cp) else 0.0), f"retorno {pct(r_ch, 2, sign=True)} x campeão {pct(r_cp, 2, sign=True)}")
    d_ch, d_cp = _f(challenger.get("drawdown_max_pct")), _f(champion.get("drawdown_max_pct"))
    check(not math.isnan(d_ch) and d_ch >= (d_cp if not math.isnan(d_cp) else 0.0) - a.tolerancia_drawdown_pp,
          f"drawdown {pct(d_ch, 2)} x campeão {pct(d_cp, 2)} (não pode piorar mais que {num(a.tolerancia_drawdown_pp, 2)} ponto)")
    if a.bloquear_promocao_com_drift:
        check(not drift, "sem mudança de regime nos dados recentes" if not drift else "mudança de regime detectada nos dados recentes")
    return Verdict(not bad, ok, bad)

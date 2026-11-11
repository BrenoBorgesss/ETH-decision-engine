"""Regra de confiabilidade: uma COMPRA só é firme se a estratégia provou valor no backtest.

Duas camadas decidem se o agente diz COMPRAR ou manda aguardar:
1. Por sinal: a confiança do sinal precisa passar do mínimo do regime (`estrategia.confianca_minima`).
2. Pela estratégia (este módulo): o resultado fora da amostra do último backtest do par precisa
   atender aos critérios de `confiabilidade` (operações mínimas, média por operação, fator de lucro e
   o limite inferior do intervalo de 95% da média). Enquanto não atender, toda compra vira
   "AGUARDAR (em teste)", com o motivo. Vendas (saídas de posição) nunca são bloqueadas por isto.

O relatório só vale se foi gerado com os MESMOS parâmetros de agora (hash gravado na procedência do
backtest) e não é mais velho que `idade_max_relatorio_dias`.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from cryptoagent.config import Config
from cryptoagent.fmt import num, pct

DAY_MS = 86_400_000
PREFIX = "AGUARDAR (estratégia em teste)"


@dataclass(frozen=True)
class Reliability:
    """Resultado da verificação: se a estratégia está validada para o par e, se não, por quê."""
    ok: bool
    reasons: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    @property
    def block_reason(self) -> str | None:
        """Texto da trava que vai para a decisão (None se validada)."""
        return None if self.ok else f"{PREFIX}: " + "; ".join(self.reasons)


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def check_reliability(cfg: Config, trade_symbol: str, now_ms: int) -> Reliability:
    """Confere o último backtest do par contra os critérios de `confiabilidade`."""
    from cryptoagent.backtest.provenance import parameters, params_hash

    c = cfg.confiabilidade
    if not c.exigir_validacao:
        return Reliability(True)
    folder = cfg.path(cfg.backtest.relatorio_pasta)
    report, meta = _read(folder / "backtest.json"), _read(folder / "backtest.meta.json")
    if not report or trade_symbol not in report:
        return Reliability(False, [f"não há backtest de {trade_symbol} (rode: cryptoagent backtest)"])
    if not meta or meta.get("parametros_sha256") != params_hash(parameters(cfg)):
        return Reliability(False, ["o último backtest foi feito com outros parâmetros (rode de novo: cryptoagent backtest)"])
    age_days = (now_ms - int(meta.get("gerado_em_ms", 0))) / DAY_MS
    if age_days > c.idade_max_relatorio_dias:
        return Reliability(False, [f"o último backtest tem {age_days:.0f} dias (máximo {c.idade_max_relatorio_dias}); rode de novo"])

    m = (report[trade_symbol].get("padrao") or {}).get("metricas") or {}
    n = int(m.get("operacoes") or 0)
    exp = m.get("expectativa_pct")
    pf = m.get("fator_lucro")
    lo = (m.get("expectativa_ic95_pct") or [None, None])[0]
    reasons = []
    if n < c.operacoes_min:
        reasons.append(f"{n} operações fora da amostra (mínimo {c.operacoes_min})")
    if exp is None or exp <= c.expectativa_min_pct:
        reasons.append(f"média por operação {pct(exp, 3, sign=True) if exp is not None else '-'} (precisa passar de {pct(c.expectativa_min_pct, 3, sign=True)})")
    if pf is None or pf < c.fator_lucro_min:
        reasons.append(f"fator de lucro {num(pf, 2) if pf is not None else '-'} (mínimo {num(c.fator_lucro_min, 2)})")
    if lo is None or (isinstance(lo, float) and math.isnan(lo)) or lo <= c.ic_inferior_min_pct:
        reasons.append(
            f"intervalo de 95% da média começa em {pct(lo, 3, sign=True) if lo is not None else '-'}: "
            f"não dá para afirmar ganho acima de {pct(c.ic_inferior_min_pct, 3, sign=True)}"
        )
    metrics = {"operacoes": n, "expectativa_pct": exp, "fator_lucro": pf, "ic95_inferior_pct": lo}
    return Reliability(not reasons, reasons, metrics)

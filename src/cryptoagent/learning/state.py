"""O que o modo ao vivo usa do último ciclo de reavaliação.

- Campeão: se for uma hipótese ("regras" com sobreposição), a análise ao vivo usa a estratégia dela;
  se for o modelo, a decisão de compra passa a ser "momento candidato com probabilidade >= limiar".
- Panorama do momento e fator de "cenário inédito" (menos casos no histórico = menos confiança).
- Mudança de regime detectada no último ciclo: vira um aviso de dados (reduz a confiança).

Lido do disco só quando os arquivos mudam. Sem ciclo rodado, nada muda no comportamento.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from cryptoagent.config import Config, apply_overlay
from cryptoagent.engine.context import DecisionContext
from cryptoagent.engine.decision import ConfidencePart, Decision
from cryptoagent.fmt import num
from cryptoagent.learning import panorama as pano
from cryptoagent.learning import registry as regmod
from cryptoagent.model.features import extract_features, is_candidate
from cryptoagent.model.logistic import LogisticModel


@dataclass(frozen=True)
class LearningState:
    """Campeão atual, modelo (se o campeão for o modelo), panorama e alerta de mudança de regime."""

    champion: regmod.Version | None
    model: LogisticModel | None
    panorama: dict | None
    drift_summary: str

    @property
    def is_default(self) -> bool:
        """True se o campeão é o motor de regras com o config atual (ou se nunca houve ciclo)."""
        return self.champion is None or (self.champion.kind == "regras" and self.champion.source is None)


_CACHE: dict[str, tuple[tuple, LearningState]] = {}
EMPTY = LearningState(None, None, None, "")


def _mtime(p: Path) -> float:
    return p.stat().st_mtime_ns if p.is_file() else 0


def load_state(cfg: Config) -> LearningState:
    """Estado do aprendizado para o modo ao vivo (com cache pela data dos arquivos)."""
    folder = cfg.path(cfg.aprendizado.pasta)
    reg_path, pan_path = folder / regmod.REGISTRY_FILE, folder / pano.PANORAMA_FILE
    key = (_mtime(reg_path), _mtime(pan_path))
    hit = _CACHE.get(str(folder))
    if hit and hit[0] == key:
        return hit[1]
    if key == (0, 0):
        state = EMPTY
    else:
        reg = regmod.load(folder)
        champion = reg.champion if reg.versions else None
        model = None
        if (
            champion is not None
            and champion.kind == "modelo"
            and champion.last
            and champion.last.metrics.get("arquivo_modelo")
        ):
            model = LogisticModel.load(folder / "modelos" / champion.last.metrics["arquivo_modelo"])
        pan = pano.load(folder)
        drift = (pan or {}).get("drift") or {}
        state = LearningState(champion, model, pan, drift.get("resumo", "") if drift.get("alerta") else "")
    _CACHE[str(folder)] = (key, state)
    return state


def champion_config(cfg: Config, state: LearningState) -> Config:
    """Config da estratégia campeã: o atual, ou o atual + a hipótese que foi promovida."""
    if state.champion is not None and state.champion.kind == "regras" and state.champion.source:
        return apply_overlay(cfg, cfg.path(state.champion.source))
    return cfg


def champion_metrics(state: LearningState) -> dict | None:
    """Métricas fora da amostra do campeão na última avaliação, se ele não for o padrão."""
    if state.is_default or state.champion is None or state.champion.last is None:
        return None
    ev = state.champion.last
    return {**ev.metrics, "_avaliado_ms": ev.evaluated_ms, "_params_sha256": ev.params_sha256}


def apply_model_champion(d: Decision, ctx: DecisionContext, cfg: Config, state: LearningState) -> Decision:
    """Com o modelo como campeão: em momento candidato, COMPRAR se a probabilidade passar do limiar; senão ESPERAR.

    Só substitui as travas de ESTRATÉGIA (regime, tendência, confiança): as de segurança e de custo continuam,
    assim como a regra de confiabilidade (estratégia em teste). Vendas e posições abertas não mudam.
    """
    model = state.model
    if model is None or d.action == "VENDER" or d.position_note is not None or not is_candidate(d):
        return d
    prob = model.predict_one(extract_features(ctx, d, cfg.estrategia, cfg.fuso.local))
    thr = cfg.modelo.limiar_probabilidade
    part = ConfidencePart(
        "modelo (campeão)",
        prob,
        f"probabilidade de resultado positivo depois dos custos {num(prob * 100, 0)}% (limiar {num(thr * 100, 0)}%)",
    )
    d = replace(d, parts=[*d.parts, part])
    if prob >= thr and "validacao" not in d.strategy_gates:
        d = replace(
            d,
            action="COMPRAR",
            blocks=[],
            strategy_gates={},
            confidence=prob * 100,
            reasons_for=[
                f"modelo campeão: probabilidade {num(prob * 100, 0)}% >= {num(thr * 100, 0)}%",
                *d.reasons_for,
            ],
        )
    elif prob < thr:
        text = f"modelo campeão: probabilidade {num(prob * 100, 0)}% abaixo do limiar de {num(thr * 100, 0)}%"
        d = replace(d, action="ESPERAR", blocks=[*d.blocks, text], strategy_gates={**d.strategy_gates, "modelo": text})
    return d


def moment_view(
    ctx_regime: str | None, hour: float, atr_pctl: float | None, cfg: Config, state: LearningState
) -> pano.MomentView:
    """Panorama do cenário de agora, com o fator de cenário inédito."""
    a = cfg.aprendizado
    return pano.moment(
        state.panorama, ctx_regime, hour, atr_pctl, min_cases=a.casos_min_cenario, floor=a.fator_cenario_inedito
    )


def clear_cache() -> None:
    """Esquece o estado lido (usado nos testes)."""
    _CACHE.clear()

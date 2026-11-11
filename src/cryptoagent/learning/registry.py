"""Registro de versões das estratégias: quem é o campeão, quem foi testado, com quais números e por quê.

Arquivo JSON legível (`aprendizado.pasta/registro.json`), gravado com troca atômica. Cada versão tem:
- tipo: "regras" (o motor de regras com um config, opcionalmente com uma hipótese aplicada) ou
  "modelo" (o motor de regras com o filtro do modelo de regressão logística);
- origem: o arquivo de hipótese ou o arquivo do modelo;
- o hash dos parâmetros e dos dados usados na avaliação;
- as métricas fora da amostra de CADA avaliação (um ciclo pode reavaliar o mesmo candidato de novo);
- o estado: campeao, desafiante, rejeitado ou aposentado (ex-campeão), com o motivo.

A versão 1 é sempre o motor de regras com o config atual: o campeão inicial.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

REGISTRY_FILE = "registro.json"
REGISTRY_VERSION = 1
STATES = ("campeao", "desafiante", "rejeitado", "aposentado")


@dataclass
class Evaluation:
    """Uma avaliação de uma versão: quando, até quando havia dados, o período fora da amostra e as métricas."""

    cycle: int
    evaluated_ms: int
    until_ms: int
    oos_start_ms: int
    oos_end_ms: int
    metrics: dict
    params_sha256: str
    data_sha256: str
    decision: str = ""  # promovido / rejeitado / mantido / campeão
    reasons: list[str] = field(default_factory=list)


@dataclass
class Version:
    """Uma versão de estratégia no registro."""

    id: int
    kind: str  # "regras" | "modelo"
    name: str
    source: str | None  # arquivo da hipótese (regras) ou do modelo (modelo)
    created_ms: int
    state: str
    evaluations: list[Evaluation] = field(default_factory=list)
    promoted_ms: int | None = None

    @property
    def last(self) -> Evaluation | None:
        """Avaliação mais recente."""
        return self.evaluations[-1] if self.evaluations else None


@dataclass
class Registry:
    """Todas as versões e o histórico dos ciclos de reavaliação."""

    versions: list[Version] = field(default_factory=list)
    cycles: list[dict] = field(default_factory=list)  # um resumo por ciclo (gatilho, quando, resultado)

    @property
    def champion(self) -> Version:
        """A versão campeã atual (existe sempre depois do primeiro ciclo)."""
        return next(v for v in self.versions if v.state == "campeao")

    def find(self, kind: str, source: str | None) -> Version | None:
        """Versão com o mesmo tipo e origem (para não duplicar a mesma hipótese a cada ciclo)."""
        return next((v for v in self.versions if v.kind == kind and v.source == source), None)

    def add(self, kind: str, name: str, source: str | None, now_ms: int, state: str = "desafiante") -> Version:
        """Acrescenta uma versão nova e devolve-a."""
        if state not in STATES:
            raise ValueError(f"estado desconhecido: {state}")
        v = Version(len(self.versions) + 1, kind, name, source, now_ms, state)
        self.versions.append(v)
        return v

    def promote(self, challenger: Version, now_ms: int) -> Version:
        """Troca o campeão: o atual vira "aposentado" e o desafiante vira "campeao". Devolve o ex-campeão."""
        old = self.champion
        old.state = "aposentado"
        challenger.state = "campeao"
        challenger.promoted_ms = now_ms
        return old


def load(folder: Path) -> Registry:
    """Lê o registro (vazio se ainda não existe)."""
    path = folder / REGISTRY_FILE
    if not path.is_file():
        return Registry()
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("versao") != REGISTRY_VERSION:
        raise ValueError(f"registro de versões {path} em formato desconhecido")
    versions = [
        Version(**{**v, "evaluations": [Evaluation(**e) for e in v.get("evaluations", [])]})
        for v in data.get("versoes", [])
    ]
    return Registry(versions, data.get("ciclos", []))


def save(folder: Path, reg: Registry) -> Path:
    """Grava o registro com troca atômica. Devolve o caminho."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / REGISTRY_FILE
    payload = clean_json(
        {"versao": REGISTRY_VERSION, "versoes": [asdict(v) for v in reg.versions], "ciclos": reg.cycles}
    )
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def clean_json(obj: object) -> object:
    """Troca NaN e infinito por None (JSON não aceita esses valores)."""
    if isinstance(obj, float):
        return None if math.isnan(obj) or math.isinf(obj) else obj
    if isinstance(obj, dict):
        return {str(k): clean_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean_json(v) for v in obj]
    return obj

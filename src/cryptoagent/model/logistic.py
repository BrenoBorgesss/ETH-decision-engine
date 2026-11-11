"""Regressão logística: estima a probabilidade de uma compra candidata terminar no lucro, depois dos custos.

- Treino com scikit-learn (padronização + regressão logística com regularização L2), semente fixa.
- O modelo treinado é salvo em JSON legível: nomes das variáveis, médias e desvios da padronização,
  coeficientes, intercepto, período de treino, métricas e a origem dos dados. Nada de pickle: o arquivo
  pode ser lido, comparado no git e carregado sem executar código.
- A previsão usa só numpy (o scikit-learn é necessário apenas para treinar).
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

MODEL_FILE_VERSION = 1


@dataclass
class LogisticModel:
    """Um modelo treinado, pronto para prever e para ser salvo."""
    features: list[str]
    mean: list[float]  # média de cada variável no treino (também usada para preencher valores ausentes)
    scale: list[float]  # desvio de cada variável no treino
    coef: list[float]  # coeficientes, na escala padronizada
    intercept: float
    trained_from_ms: int
    trained_until_ms: int  # nenhum exemplo de treino tem resultado conhecido depois disto
    n_train: int
    base_rate: float  # fração de exemplos positivos no treino
    seed: int
    regularization_c: float
    info: dict = field(default_factory=dict)  # métricas e origem (par, hash dos dados, commit...)

    def predict_proba(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        """Probabilidade de resultado positivo para cada linha (colunas na ordem de `features`)."""
        x = np.asarray(X[self.features] if isinstance(X, pd.DataFrame) else X, dtype=float)
        mean, scale = np.asarray(self.mean), np.asarray(self.scale)
        x = np.where(np.isnan(x), mean, x)
        z = ((x - mean) / scale) @ np.asarray(self.coef) + self.intercept
        return 1 / (1 + np.exp(-z))

    def predict_one(self, features: dict[str, float]) -> float:
        """Probabilidade para um único momento, dado como {variável: valor}."""
        return float(self.predict_proba(np.array([[features.get(f, math.nan) for f in self.features]]))[0])

    def to_dict(self) -> dict:
        """Forma JSON do modelo."""
        return {"versao": MODEL_FILE_VERSION, "tipo": "regressao_logistica", **asdict(self)}

    def save(self, path: Path) -> None:
        """Grava o modelo em JSON (troca atômica)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> LogisticModel:
        """Lê um modelo gravado por `save`. Levanta ValueError se a versão do arquivo for desconhecida."""
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.pop("versao", None) != MODEL_FILE_VERSION or data.pop("tipo", None) != "regressao_logistica":
            raise ValueError(f"arquivo de modelo {path} em formato desconhecido")
        return cls(**data)


def train(
    df: pd.DataFrame, features: list[str] | tuple[str, ...], *, seed: int, regularization_c: float, info: dict | None = None
) -> LogisticModel:
    """Treina a regressão logística nas linhas de `df` (colunas `features` e `rotulo`, mais `t_ms` e `fim_ms`)."""
    from sklearn.linear_model import LogisticRegression

    features = list(features)
    y = df["rotulo"].to_numpy(dtype=int)
    if len(np.unique(y)) < 2:
        raise ValueError("o treino precisa de exemplos positivos e negativos")
    X = df[features].to_numpy(dtype=float)
    mean = np.nanmean(X, axis=0)
    mean = np.where(np.isnan(mean), 0.0, mean)
    X = np.where(np.isnan(X), mean, X)
    scale = X.std(axis=0)
    scale = np.where(scale > 0, scale, 1.0)
    clf = LogisticRegression(C=regularization_c, max_iter=2000, random_state=seed)
    clf.fit((X - mean) / scale, y)
    return LogisticModel(
        features=features,
        mean=mean.tolist(),
        scale=scale.tolist(),
        coef=clf.coef_[0].tolist(),
        intercept=float(clf.intercept_[0]),
        trained_from_ms=int(df["t_ms"].min()),
        trained_until_ms=int(df["fim_ms"].max()),
        n_train=len(df),
        base_rate=float(y.mean()),
        seed=seed,
        regularization_c=regularization_c,
        info=info or {},
    )

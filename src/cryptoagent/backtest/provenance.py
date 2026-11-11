"""Procedência de uma execução: o que é preciso para repetir um backtest ou um treino e obter o mesmo resultado.

Grava, num JSON ao lado do relatório:
- versão do programa, commit do git (e se havia alterações não commitadas), versões do Python e das bibliotecas;
- os parâmetros usados (estratégia, indicadores, regime, backtest, modelo) e o hash deles;
- os custos usados e, se vieram de um arquivo congelado, o caminho e o hash dele;
- cada arquivo de histórico lido: linhas, primeiro e último candle e o hash SHA-256;
- a semente e o hash do relatório gerado.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from importlib import metadata
from pathlib import Path

import pandas as pd

from cryptoagent import __version__
from cryptoagent.config import Config
from cryptoagent.timeutils import now_ms

PARAM_SECTIONS = ("estrategia", "indicadores", "regime", "backtest", "modelo", "fuso")
LIBRARIES = ("numpy", "pandas", "pyarrow", "scikit-learn", "pydantic")


def sha256_file(path: Path) -> str:
    """SHA-256 do conteúdo de um arquivo."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(root: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip()


def git_state(root: Path) -> dict:
    """Commit atual e se há alterações não commitadas em arquivos versionados. Vazio se não houver git."""
    commit = _git(root, "rev-parse", "HEAD")
    if commit is None:
        return {}
    dirty = _git(root, "status", "--porcelain", "--untracked-files=no")
    return {"commit": commit, "alteracoes_nao_commitadas": bool(dirty)}


def parameters(cfg: Config) -> dict:
    """Seções de parâmetros do config que mudam o resultado, como dicionário."""
    return {name: getattr(cfg, name).model_dump(mode="json") for name in PARAM_SECTIONS}


def params_hash(params: dict) -> str:
    """Hash dos parâmetros (JSON com chaves ordenadas)."""
    return hashlib.sha256(json.dumps(params, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def history_files(cfg: Config, symbols: list[str]) -> list[Path]:
    """Arquivos de histórico que um backtest desses pares lê (os que existem)."""
    folder = cfg.path(cfg.historico.pasta)
    tfs = set(cfg.estrategia.timeframes) | {"1m", cfg.sazonalidade.timeframe}
    paths = {folder / s / f"{tf}.parquet" for s in symbols for tf in tfs}
    return sorted(p for p in paths if p.is_file())


def describe_file(path: Path, root: Path) -> dict:
    """Linhas, primeiro e último candle e hash de um arquivo de histórico."""
    df = pd.read_parquet(path, columns=["open_time"])
    try:
        shown = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        shown = path.name
    return {
        "arquivo": shown,
        "linhas": len(df),
        "primeiro_ms": int(df["open_time"].iloc[0]) if len(df) else None,
        "ultimo_ms": int(df["open_time"].iloc[-1]) if len(df) else None,
        "sha256": sha256_file(path),
    }


def build(
    cfg: Config, *, symbols: list[str], costs: dict, frozen_costs: Path | None, outputs: list[Path], kind: str
) -> dict:
    """Monta o registro de procedência de uma execução."""
    params = parameters(cfg)
    libs = {}
    for lib in LIBRARIES:
        try:
            libs[lib] = metadata.version(lib)
        except metadata.PackageNotFoundError:
            libs[lib] = None
    return {
        "tipo": kind,
        "gerado_em_ms": now_ms(),
        "programa": {
            "cryptoagent": __version__,
            **git_state(cfg.root),
            "python": platform.python_version(),
            "bibliotecas": libs,
        },
        "semente": cfg.modelo.semente,
        "parametros_sha256": params_hash(params),
        "parametros": params,
        "custos": costs,
        "custos_congelados": {"arquivo": frozen_costs.name, "sha256": sha256_file(frozen_costs)}
        if frozen_costs and frozen_costs.is_file()
        else None,
        "dados": [describe_file(p, cfg.root) for p in history_files(cfg, symbols)],
        "saidas": [{"arquivo": p.name, "sha256": sha256_file(p)} for p in outputs if p.is_file()],
    }


def save(record: dict, path: Path) -> None:
    """Grava o registro de procedência em JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")

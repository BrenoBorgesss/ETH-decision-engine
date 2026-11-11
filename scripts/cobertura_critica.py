"""Cobertura de testes das partes críticas, a partir do coverage.json do pytest-cov.

Uso:
    python -m pytest --cov --cov-report=json:coverage.json
    python scripts/cobertura_critica.py coverage.json [--minimo 85] [--markdown docs/COBERTURA.md]

A porcentagem de cada área soma linhas e ramos (if/else) cobertos. Com `--minimo`, sai com código 1
se alguma área ficar abaixo do mínimo (usado no CI).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Área -> arquivos (relativos a src/cryptoagent; terminados em "/" = pasta inteira)
AREAS: dict[str, list[str]] = {
    "Coleta de dados": [
        "data/history.py",
        "data/rest.py",
        "data/retry.py",
        "data/validation.py",
        "data/health.py",
        "data/websocket.py",
        "data/market_store.py",
        "data/models.py",
    ],
    "Indicadores": ["indicators/"],
    "Sinais e decisão": [
        "engine/signals.py",
        "engine/rules.py",
        "engine/decision.py",
        "engine/context.py",
        "engine/live.py",
    ],
    "Backtest": ["backtest/"],
    "Carteira virtual": ["paper/", "storage/sim_db.py"],
    "Registro de operações": [
        "account/journal.py",
        "account/ledger.py",
        "engine/journal.py",
        "storage/agent_db.py",
        "dashboard/views.py",
    ],
    "Promoção de modelo": ["learning/", "model/", "engine/reliability.py"],
    "Risco": ["account/limits.py", "session/daily.py", "account/status.py", "account/permissions.py"],
    "Segurança (somente leitura, segredos)": ["security/redact.py", "data/rest.py", "account/permissions.py"],
}


def _matches(path: str, patterns: list[str]) -> bool:
    return any(path == p or (p.endswith("/") and path.startswith(p)) for p in patterns)


def area_table(cov: dict) -> list[dict]:
    """Uma linha por área: linhas e ramos cobertos, porcentagem e o arquivo menos coberto."""
    files = {k.replace("\\", "/").split("src/cryptoagent/")[-1]: v["summary"] for k, v in cov["files"].items()}
    rows = []
    for area, patterns in AREAS.items():
        chosen = {f: s for f, s in files.items() if _matches(f, patterns)}
        st = sum(s["num_statements"] for s in chosen.values())
        mi = sum(s["missing_lines"] for s in chosen.values())
        br = sum(s.get("num_branches", 0) for s in chosen.values())
        bm = sum(s.get("missing_branches", 0) for s in chosen.values())
        worst = (
            min(chosen.items(), key=lambda kv: kv[1]["percent_covered"]) if chosen else ("-", {"percent_covered": 0})
        )
        rows.append(
            {
                "area": area,
                "linhas": (st - mi, st),
                "ramos": (br - bm, br),
                "pct": 100 * (st - mi + br - bm) / (st + br) if st + br else 100.0,
                "pior": (worst[0], worst[1]["percent_covered"]),
                "arquivos": len(chosen),
            }
        )
    return rows


def to_markdown(rows: list[dict], total: float) -> str:
    """Tabela em Markdown para a documentação."""
    out = [
        "| Área | Linhas | Ramos (if/else) | Cobertura | Arquivo menos coberto |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        out.append(
            f"| {r['area']} | {r['linhas'][0]}/{r['linhas'][1]} | {r['ramos'][0]}/{r['ramos'][1]} | "
            f"**{r['pct']:.1f}%** | `{r['pior'][0]}` ({r['pior'][1]:.1f}%) |"
        )
    out.append(f"| Pacote inteiro (inclui telas do terminal) | | | {total:.1f}% | |")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    """Imprime a tabela; com `--minimo`, falha se alguma área ficar abaixo."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("arquivo", type=Path, help="coverage.json gerado pelo pytest-cov")
    ap.add_argument("--minimo", type=float, default=None, help="porcentagem mínima por área")
    ap.add_argument("--markdown", type=Path, default=None, help="grava também a tabela em Markdown neste arquivo")
    args = ap.parse_args(argv)
    cov = json.loads(args.arquivo.read_text(encoding="utf-8"))
    rows = area_table(cov)
    total = float(cov["totals"]["percent_covered"])
    for r in rows:
        print(
            f"{r['area']:40s} linhas {r['linhas'][0]:5d}/{r['linhas'][1]:<5d} "
            f"ramos {r['ramos'][0]:4d}/{r['ramos'][1]:<4d} -> {r['pct']:5.1f}%   (menor: {r['pior'][0]} {r['pior'][1]:.1f}%)"
        )
    print(f"{'Pacote inteiro':40s} {total:.1f}%")
    if args.markdown:
        args.markdown.write_text(to_markdown(rows, total) + "\n", encoding="utf-8")
    if args.minimo is not None:
        low = [r for r in rows if r["pct"] < args.minimo]
        for r in low:
            print(f"ABAIXO DO MÍNIMO ({args.minimo:g}%): {r['area']} com {r['pct']:.1f}%")
        return 1 if low else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())

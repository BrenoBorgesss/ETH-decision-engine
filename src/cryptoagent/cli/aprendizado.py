"""Comandos `aprendizado reavaliar`, `aprendizado versoes` e `aprendizado panorama`."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from rich.markup import escape
from rich.table import Table

from cryptoagent.cli._shared import console
from cryptoagent.config import Config, Secrets
from cryptoagent.fmt import pct
from cryptoagent.timeutils import format_local, trading_day_start_ms


def print_cycle(cfg: Config, r: object) -> None:
    """Mostra o resultado de um ciclo de reavaliação."""
    tz = cfg.fuso.local
    console.rule(f"Ciclo {r.number} ({r.trigger}): dados até {format_local(r.until_ms, tz, '%d/%m/%Y %H:%M')}")
    console.print(
        f"Fora da amostra: {format_local(r.oos_start, tz, '%d/%m/%Y')} a {format_local(r.oos_end, tz, '%d/%m/%Y')} | "
        f"{r.dataset_size} momentos candidatos ({r.sim_examples} vindos da simulação)"
    )
    t = Table(title="Campeão x desafiantes (mesmo período, já com custos)")
    for col in ("Versão", "Operações", "Retorno", "Drawdown", "Média/operação", "IC 95% da média", "Decisão"):
        t.add_column(col)
    for e in r.entries:
        m = e.metrics
        lo, hi = (m.get("expectativa_ic95_pct") or [None, None])[:2]
        ic = f"{pct(lo, 3, sign=True)} a {pct(hi, 3, sign=True)}" if lo is not None else "-"
        dec = e.version.last.decision if e.version.last else ""
        style = "green" if dec == "promovido" else "red" if dec == "rejeitado" else ""
        t.add_row(f"v{e.version.id} {e.version.name}", str(m.get("operacoes", 0)), pct(m.get("retorno_total_pct"), 2, sign=True),
                  pct(m.get("drawdown_max_pct"), 2), pct(m.get("expectativa_pct"), 3, sign=True), ic, f"[{style}]{dec}[/]" if style else dec)
    console.print(t)
    for e in r.entries:
        ev = e.version.last
        if ev and ev.decision not in ("campeão (referência)",) and ev.reasons:
            label = "não atendeu" if ev.decision == "rejeitado" else "atendeu"
            console.print(f"v{e.version.id} {label}: " + escape("; ".join(ev.reasons)) + (f" [dim]({escape(e.note)})[/]" if e.note else ""))
    for i in r.drift.items:
        console.print(f"{'[yellow]⚠[/] ' if i.alert else '  '}{i.name}: {i.detail}")
    if r.promoted:
        console.print(f"[bold green]Campeão trocado:[/] v{r.champion_before.id} → v{r.champion_after.id} ({r.champion_after.name}).")
    else:
        console.print(f"Campeão mantido: v{r.champion_after.id} ({r.champion_after.name}).")
    console.print(f"[dim]Relatório com panoramas e calibração: {r.report_path}[/]")


def _until(cfg: Config, text: str | None) -> int | None:
    if not text:
        return None
    return trading_day_start_ms(date.fromisoformat(text), cfg.fuso.local)


async def cmd_learning_cycle(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    """Comando `aprendizado reavaliar`: roda um ciclo (campeão x desafiantes, drift, panoramas). Devolve o código de saída."""
    from cryptoagent.cli.backtest import resolve_costs
    from cryptoagent.learning.cycle import run_cycle

    frozen = Path(args.custos_arquivo) if args.custos_arquivo else None
    costs = (await resolve_costs(cfg, secrets, [cfg.paper.par], frozen))[0][cfg.paper.par]
    until = _until(cfg, args.ate)
    r = run_cycle(cfg, costs, until_ms=until, trigger="manual" + (f" (até {args.ate})" if args.ate else ""),
                  sim_db=cfg.path(cfg.paper.banco), clock_ms=until, progress=lambda m: console.print(f"[dim]{escape(m)}[/]"))
    print_cycle(cfg, r)
    return 0


async def cmd_learning_versions(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    """Comando `aprendizado versoes`: histórico de versões com as métricas de cada avaliação. Devolve o código de saída."""
    from cryptoagent.learning import registry

    reg = registry.load(cfg.path(cfg.aprendizado.pasta))
    if not reg.versions:
        console.print("Nenhum ciclo de reavaliação ainda. Rode: cryptoagent aprendizado reavaliar")
        return 1
    tz = cfg.fuso.local
    t = Table(title="Versões da estratégia")
    for col in ("Versão", "Estado", "Ciclo", "Dados até", "Operações", "Retorno", "Drawdown", "Média/operação", "Decisão"):
        t.add_column(col)
    for v in reg.versions:
        for ev in v.evaluations or [None]:
            if ev is None:
                t.add_row(f"v{v.id} {v.name}", v.state, "-", "-", "-", "-", "-", "-", "-")
                continue
            m = ev.metrics
            t.add_row(f"v{v.id} {v.name}", v.state, str(ev.cycle), format_local(ev.until_ms, tz, "%d/%m/%Y"), str(m.get("operacoes", 0)),
                      pct(m.get("retorno_total_pct"), 2, sign=True), pct(m.get("drawdown_max_pct"), 2), pct(m.get("expectativa_pct"), 3, sign=True), ev.decision)
    console.print(t)
    console.print(f"Campeão atual: v{reg.champion.id} {reg.champion.name}" + (
        f" (promovido em {format_local(reg.champion.promoted_ms, tz, '%d/%m/%Y %H:%M')})" if reg.champion.promoted_ms else ""))
    return 0


async def cmd_learning_panorama(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    """Comando `aprendizado panorama`: panorama por cenário e calibração do último ciclo. Devolve o código de saída."""
    from cryptoagent.learning import panorama as pano

    folder = cfg.path(cfg.aprendizado.pasta)
    pan = pano.load(folder)
    if pan is None:
        console.print("Nenhum panorama ainda. Rode: cryptoagent aprendizado reavaliar")
        return 1
    g = pan.get("geral") or {}
    console.print(f"Panorama com {pan['momentos']} momentos candidatos até {format_local(pan['ate_ms'], cfg.fuso.local, '%d/%m/%Y')}: "
                  f"acerto {pct((g.get('acerto') or 0) * 100, 1)}, média por operação {pct(g.get('media_pct'), 3, sign=True)}.")
    t = Table(title=f"Cenários com mais casos (de {len(pan['cenarios'])})")
    for col in ("Cenário", "Casos", "Acerto (IC 95%)", "Ganho médio", "Perda média", "Média/operação (IC 95%)"):
        t.add_column(col)
    for key, s in list(pan["cenarios"].items())[: args.n]:
        lo, hi = s["acerto_ic95"]
        mlo, mhi = s["media_ic95_pct"]
        t.add_row(key, str(s["casos"]), f"{pct(s['acerto'] * 100, 0)} ({pct(lo * 100, 0)}–{pct(hi * 100, 0)})", pct(s["ganho_medio_pct"], 2, sign=True),
                  pct(s["perda_media_pct"], 2, sign=True), f"{pct(s['media_pct'], 3, sign=True)} ({pct(mlo, 3, sign=True)} a {pct(mhi, 3, sign=True)})")
    console.print(t)
    c = Table(title="Calibração da confiança das regras")
    for col in ("Faixa de confiança", "Casos", "Confiança média", "Acerto real (IC 95%)"):
        c.add_column(col)
    for row in pan.get("calibracao_regras", []):
        lo, hi = row["acerto_ic95"]
        c.add_row(row["faixa"], str(row["casos"]), pct(row["confianca_media"], 0), f"{pct(row['acerto'] * 100, 0)} ({pct(lo * 100, 0)}–{pct(hi * 100, 0)})")
    console.print(c)
    console.print(f"[dim]Gráficos de calibração: {folder / 'aprendizado.html'}[/]")
    return 0


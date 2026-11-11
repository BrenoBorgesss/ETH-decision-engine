"""Comandos `paper rodar`, `paper relatorio`, `paper replay` e `paper resetar` (carteira virtual em R$)."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
from rich.markup import escape
from rich.table import Table

from cryptoagent.cli._shared import _account_taker_fees, console
from cryptoagent.config import Config, Secrets
from cryptoagent.data import history
from cryptoagent.data.rest import BinanceReadOnlyClient
from cryptoagent.storage import sim_db
from cryptoagent.timeutils import format_local, now_ms

if TYPE_CHECKING:  # só para as anotações: importar de verdade aqui deixaria a inicialização mais lenta
    from cryptoagent.paper.report import VariantReport

# ----------------------------------------------------------- paper trading


async def cmd_paper_run(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    """Comando `paper rodar`: roda o paper trading com a carteira virtual até ser interrompido. Devolve o código de saída do processo."""
    from cryptoagent.paper.runner import run_paper

    fees = await _account_taker_fees(cfg, secrets, [cfg.paper.par])
    fee, source = fees.get(cfg.paper.par, (cfg.custos.taxa_padrao_taker, "do config"))
    console.print(f"[dim]Taxa usada na carteira virtual: {fee * 100:.4f}% por lado ({source}). Banco da simulação: {cfg.path(cfg.paper.banco)}[/]")
    await run_paper(
        cfg, secrets, fee=fee, capital=args.capital, max_loss_brl=args.perda_max, max_cycles=args.ciclos, say=lambda msg: console.print(escape(msg)),
        auto_learning=True,
    )
    return 0


def _print_gate_study(rep: VariantReport, min_trades: int) -> None:
    from cryptoagent.fmt import pct
    from cryptoagent.paper.report import GATE_PT

    console.print(
        "[bold]Variante de ESTUDO:[/] compra virtual mesmo quando só travas de previsão seguraram "
        "(tendência, regime, esticado, gatilho, confiança). As de segurança e as de custo continuam valendo."
    )
    if not rep.gates:
        console.print("[dim]Nenhuma operação de estudo fechada ainda.[/]")
        return
    t = Table(title="Cada trava: operações que ela teria BARRADO x as que deixaria passar (% líquido, com taxas)")
    for col in ("Trava", "Barradas: n", "acerto", "média", "Liberadas: n", "acerto", "média"):
        t.add_column(col)
    def f(v: float | None, s: bool = False) -> str:
        return "-" if v is None else pct(v, 3 if s else 0, sign=s)
    for g in rep.gates:
        t.add_row(GATE_PT[g.gate], str(g.n_blocked), f(g.win_blocked_pct), f(g.avg_blocked_pct, True), str(g.n_passed), f(g.win_passed_pct), f(g.avg_passed_pct, True))
    console.print(t)
    console.print(
        "[dim]Se as 'barradas' dão média pior que as 'liberadas', a trava está ajudando. Uma trava só deve ser revista com "
        f"pelo menos {min_trades} operações em CADA lado, e como hipótese declarada testada no backtest — nunca mudando direto.[/]"
    )


def _print_variant_reports(cfg: Config, conn: sqlite3.Connection, bars: pd.DataFrame, *, now: int, costs: tuple[float, float, float]) -> list[VariantReport]:
    """Imprime o relatório de cada variante do banco da simulação e devolve os relatórios."""
    from cryptoagent.fmt import brl, pct
    from cryptoagent.paper.report import OUTCOMES, load_backtest_json, variant_report
    from cryptoagent.paper.runner import load_variants

    pc, tz = cfg.paper, cfg.fuso.local
    reports = []
    for name, vcfg in load_variants(cfg, pc.variantes):
        bt_json = load_backtest_json(vcfg.path(vcfg.backtest.relatorio_pasta) / "backtest.json")
        rep = variant_report(conn, name, pc.capital, bars, pc.horizonte_avaliacao_h, pc.operacoes_min_conclusao, bt_json, pc.par,
                             now=now, costs=costs, tz=tz)
        reports.append(rep)
        m = rep.metrics
        console.rule(f"Simulação: {name} ({pc.par}) de {format_local(rep.started_ms, tz, '%d/%m/%Y %H:%M')} a {format_local(now, tz, '%d/%m/%Y %H:%M')}")
        console.print(
            f"Patrimônio virtual: [bold]{brl(rep.equity)}[/] (início {brl(rep.initial_capital)}; resultado {brl(rep.equity - rep.initial_capital, sign=True)}) | "
            f"operações fechadas: {m['operacoes']} | acerto {pct(m['taxa_acerto_pct'], 1)} | média por operação {pct(m['expectativa_pct'], 3, sign=True)} | "
            f"drawdown {pct(m['drawdown_max_pct'], 2)} | taxas pagas {brl(m['taxas_pagas'])}"
        )
        if rep.buy_hold:
            bh = rep.buy_hold
            console.print(f"Comprar e segurar {pc.par} no mesmo período (mesmos custos): {pct(bh.get('retorno_total_pct'), 2, sign=True)} (drawdown {pct(bh.get('drawdown_max_pct'), 2)}).")
        if rep.rejected_orders:
            console.print(f"[yellow]{rep.rejected_orders} compra(s) virtual(is) recusada(s) pelas regras de tamanho mínimo do par.[/]")
        if rep.backtest_expectancy_pct is not None:
            weak = " [yellow](amostra pequena demais para valer como referência)[/]" if rep.backtest_trades < pc.operacoes_min_conclusao else ""
            console.print(f"Backtest fora da amostra: {pct(rep.backtest_expectancy_pct, 3, sign=True)} por operação, em {rep.backtest_trades} operação(ões){weak}.")
        if rep.open_trade:
            o = rep.open_trade
            console.print(f"Posição virtual aberta: entrada {brl(o['entry_price'])} | stop {brl(o['stop'])} | alvo {brl(o['target1'])} | desde {format_local(o['entry_ms'], tz)}")
        console.print("Sinais registrados: " + (", ".join(f"{k} {v}" for k, v in rep.signals_by_action.items()) or "nenhum ainda"))
        if rep.outcomes:
            t = Table(title=f"O que o mercado fez nas {pc.horizonte_avaliacao_h:g} h seguintes (usando entrada, stop e alvo de cada sinal)")
            for col in ("Sinal", "Alvo c/ lucro", "Alvo s/ lucro", "Stop primeiro", "Nenhum", "Aguardando"):
                t.add_column(col)
            for action, res in rep.outcomes.items():
                t.add_row(action, *(str(res.get(k, 0)) for k in OUTCOMES))
            console.print(t)
            console.print(
                "[dim]'ESPERAR (com níveis)' = a leitura tinha entrada/stop/alvo, mas alguma trava segurou. "
                "'Alvo s/ lucro' = o preço chegou ao alvo, mas o alvo não pagava taxas + spread: na prática, prejuízo. "
                "Só 'Alvo c/ lucro' conta como acerto.[/]"
            )
        if rep.calibration:
            c = Table(title="Calibração: a confiança acompanha a taxa de acerto?")
            for col in ("Faixa de confiança", "Sinais resolvidos", "% alvo com lucro"):
                c.add_column(col)
            for band, n, rate in rep.calibration:
                c.add_row(band, str(n), pct(rate, 0))
            console.print(c)
        if vcfg.paper.estudo:
            _print_gate_study(rep, pc.operacoes_min_conclusao)
        if not rep.enough:
            console.print(f"[yellow]Só {m['operacoes']} operação(ões) virtual(is) fechada(s) (mínimo {pc.operacoes_min_conclusao}): ainda NÃO dá para tirar conclusões.[/]")
    return reports


async def cmd_paper_report(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    """Comando `paper relatorio`: resultado de cada variante, o que o mercado fez depois de cada sinal e comprar e segurar. Devolve o código de saída."""
    pc = cfg.paper
    db = cfg.path(pc.banco)
    if not db.is_file():
        console.print(f"[yellow]Ainda não há simulação em {db}. Comece com: cryptoagent paper rodar (ou teste com: cryptoagent paper replay)[/]")
        return 1
    store = history.HistoryStore(cfg.path(cfg.historico.pasta))
    async with BinanceReadOnlyClient(cfg.binance, secrets) as client:
        await client.sync_time()
        await history.update_from_rest(store, client, pc.par, "1m", months=cfg.historico.meses, now=client.server_now_ms())
    fees = await _account_taker_fees(cfg, secrets, [pc.par])
    fee = fees.get(pc.par, (cfg.custos.taxa_padrao_taker, ""))[0]
    half = cfg.sazonalidade.spread_padrao_pct.get(pc.par, 0.05) / 200
    conn = sim_db.connect(db)
    try:
        _print_variant_reports(cfg, conn, store.load(pc.par, "1m"), now=now_ms(), costs=(fee, half, cfg.backtest.slippage_mercado_pct))
    finally:
        conn.close()
    return 0


def _remove_db(path: Path) -> None:
    for p in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        if p.exists():
            p.unlink()


async def cmd_paper_replay(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    """Comando `paper replay`: roda o paper trading sobre o histórico, com o relógio acelerado. Devolve o código de saída."""
    import time

    import numpy as np

    from cryptoagent.backtest.data import load_backtest_data
    from cryptoagent.backtest.metrics import compute_metrics
    from cryptoagent.backtest.report import COLS, _fmt
    from cryptoagent.backtest.simulator import simulate
    from cryptoagent.cli.backtest import resolve_costs
    from cryptoagent.fmt import num
    from cryptoagent.paper.replay import ReplayClient, replay_window
    from cryptoagent.paper.runner import run_paper

    pc, tz = cfg.paper, cfg.fuso.local
    if args.variantes:
        cfg = cfg.model_copy(deep=True)
        cfg.paper.variantes = list(args.variantes)
    store = history.HistoryStore(cfg.path(cfg.historico.pasta))
    start, end = replay_window(store, pc.par, args.dias, tz, args.inicio)
    frozen = Path(args.custos_arquivo) if args.custos_arquivo else None
    costs = (await resolve_costs(cfg, Secrets(), [pc.par], frozen))[0][pc.par]
    folder = cfg.path(pc.replay_pasta)
    db = folder / f"replay-{format_local(start, tz, '%Y%m%d')}-{args.dias}d.sqlite"
    _remove_db(db)  # cada replay começa do zero
    no_profile = folder / "sem_perfil_por_horario"

    def adjust(c: Config) -> Config:
        c = c.model_copy(deep=True)
        c.sazonalidade.relatorio_pasta = str(no_profile)  # perfil salvo usa dados posteriores: desligado no replay
        c.aprendizado.pasta = str(no_profile)  # campeão e panorama atuais também usam dados posteriores: desligados
        return c

    client = ReplayClient(cfg, start_ms=start, costs=costs)
    console.print(
        f"[bold]Replay[/] de {format_local(start, tz, '%d/%m/%Y %H:%M')} a {format_local(end, tz, '%d/%m/%Y %H:%M')} (horário local), "
        f"no histórico gravado, com o relógio acelerado. Mesmo laço e mesmo motor do paper ao vivo; nenhuma conexão com a internet."
    )
    lines = []

    def say(msg: str) -> None:
        lines.append(msg)
        if args.detalhes or "->" in msg or "OCO" in msg or "[aviso]" in msg or msg.startswith("Paper trading"):
            console.print(escape(msg))

    t0 = time.perf_counter()
    await run_paper(adjust(cfg), secrets, fee=costs.fee, capital=args.capital, max_loss_brl=args.perda_max, db_path=db, client=client,
                    until_ms=end, say=say, sleep=client.advance, adjust=adjust)
    elapsed = time.perf_counter() - t0
    console.print(f"[dim]Replay de {args.dias} dia(s) simulado(s) em {elapsed:.0f} s ({args.dias * 86_400 / max(elapsed, 1e-9):,.0f}× mais rápido que o tempo real); "
                  f"{sum(1 for x in lines if '] ' in x and '%' in x)} decisões. Banco: {db}[/]")

    half = float(np.mean(list(costs.spread_by_hour.values()))) / 200
    conn = sim_db.connect(db)
    try:
        reports = _print_variant_reports(cfg, conn, store.load(pc.par, "1m"), now=end, costs=(costs.fee, half, costs.slip_market_pct))
    finally:
        conn.close()

    # mesmo período no backtest (mesmos custos): o replay e o backtest deveriam contar a mesma história
    data = load_backtest_data(cfg, pc.par)
    bt = cfg.backtest
    sim = simulate(data, cfg.estrategia, cfg.indicadores, costs, capital=reports[0].initial_capital if reports else pc.capital,
                   loss_pct_day=bt.perda_max_dia_pct, divisor=cfg.risco.divisor_perda_diaria, spread_max_pct=cfg.custos.spread_max_pct,
                   start_ms=start, end_ms=end, season_on=False, label="backtest")
    window = [t for t in sim.trades if start <= t.entry_ms < end]
    t = Table(title="Mesmo período: replay (paper) x backtest, com os mesmos custos")
    t.add_column("Origem")
    for _, title, _ in COLS:
        t.add_column(title)
    for rep in reports:
        t.add_row(f"replay: {rep.variant}", *(_fmt(k, rep.metrics.get(k)) for k, _, _ in COLS))
    bm = compute_metrics(window, start, end, reports[0].initial_capital if reports else pc.capital, tz)
    t.add_row("backtest (parâmetros padrão)", *(_fmt(k, bm.get(k)) for k, _, _ in COLS))
    console.print(t)
    console.print(
        "[dim]Diferenças esperadas entre os dois: no replay a compra é no preço do momento (+ atraso de execução) e respeita o "
        f"tamanho mínimo de ordem; derivativos e perfil por horário ficam indisponíveis (cada aviso reduz a confiança em "
        f"{num(cfg.estrategia.penalidade_por_aviso_dados * 100, 0)}%); a perda máxima do dia é em R$ fixos.[/]"
    )
    return 0


async def cmd_paper_reset(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    """Comando `paper resetar`: guarda uma cópia do banco da simulação e recomeça do zero. Devolve o código de saída."""
    db = cfg.path(cfg.paper.banco)
    if not db.is_file():
        console.print(f"Não há simulação para resetar ({db} não existe).")
        return 0
    conn = sqlite3.connect(db)
    try:
        trades = conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]
        signals = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    finally:
        conn.close()
    stamp = format_local(now_ms(), "UTC", "%Y%m%d-%H%M%S")
    backup = db.with_name(f"{db.stem}_antes_reset_{stamp}{db.suffix}")
    if not args.confirmar:
        console.print(
            f"Isto vai recomeçar a carteira virtual do zero ({trades} operações e {signals} sinais em {db}). "
            f"O banco atual será guardado em {backup}.\nPara fazer: cryptoagent paper resetar --confirmar"
        )
        return 1
    src = sqlite3.connect(db)
    dst = sqlite3.connect(backup)
    try:
        src.backup(dst)  # cópia consistente, mesmo com o modo WAL
    finally:
        dst.close()
        src.close()
    _remove_db(db)
    console.print(f"Simulação recomeçada. Cópia do banco anterior ({trades} operações, {signals} sinais): {backup}")
    return 0


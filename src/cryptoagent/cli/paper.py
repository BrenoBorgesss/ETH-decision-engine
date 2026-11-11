"""Comandos `paper rodar` e `paper relatorio`."""

from __future__ import annotations

import argparse

from rich.markup import escape
from rich.table import Table

from cryptoagent.cli._shared import _account_taker_fees, console
from cryptoagent.config import Config, Secrets
from cryptoagent.data import history
from cryptoagent.data.rest import BinanceReadOnlyClient
from cryptoagent.storage import agent_db
from cryptoagent.timeutils import format_local

# ----------------------------------------------------------- paper trading


async def cmd_paper_run(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    from cryptoagent.paper.runner import run_paper

    fees = await _account_taker_fees(cfg, secrets, [cfg.paper.par])
    fee, source = fees.get(cfg.paper.par, (cfg.custos.taxa_padrao_taker, "do config"))
    console.print(f"[dim]Taxa usada na carteira virtual: {fee * 100:.4f}% por lado ({source}).[/]")
    await run_paper(cfg, secrets, fee=fee, max_cycles=args.ciclos, say=lambda msg: console.print(escape(msg)))
    return 0


def _print_gate_study(rep, min_trades: int) -> None:
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
    def f(v, s=False):
        return "-" if v is None else pct(v, 3 if s else 0, sign=s)
    for g in rep.gates:
        t.add_row(GATE_PT[g.gate], str(g.n_blocked), f(g.win_blocked_pct), f(g.avg_blocked_pct, True), str(g.n_passed), f(g.win_passed_pct), f(g.avg_passed_pct, True))
    console.print(t)
    console.print(
        "[dim]Se as 'barradas' dão média pior que as 'liberadas', a trava está ajudando. Uma trava só deve ser revista com "
        f"pelo menos {min_trades} operações em CADA lado, e como hipótese declarada testada no backtest — nunca mudando direto.[/]"
    )


async def cmd_paper_report(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    from cryptoagent.fmt import num, pct
    from cryptoagent.paper.report import OUTCOMES, load_backtest_json, variant_report
    from cryptoagent.paper.runner import load_variants

    pc = cfg.paper
    store = history.HistoryStore(cfg.path(cfg.historico.pasta))
    async with BinanceReadOnlyClient(cfg.binance, secrets) as client:
        await client.sync_time()
        await history.update_from_rest(store, client, pc.par, "1m", months=cfg.historico.meses, now=client.server_now_ms())
    bars = store.load(pc.par, "1m")
    conn = agent_db.connect(cfg.path(cfg.conta.banco))
    try:
        for name, vcfg in load_variants(cfg, pc.variantes):
            bt_json = load_backtest_json(vcfg.path(vcfg.backtest.relatorio_pasta) / "backtest.json")
            rep = variant_report(conn, name, pc.capital, bars, pc.horizonte_avaliacao_h, pc.operacoes_min_conclusao, bt_json, pc.par)
            m = rep.metrics
            console.rule(f"Paper trading: {name} ({pc.par}) desde {format_local(rep.started_ms, cfg.fuso.local, '%d/%m/%Y %H:%M')}")
            console.print(
                f"Patrimônio virtual: [bold]{num(rep.equity)}[/] (início {num(pc.capital)}) | operações fechadas: {m['operacoes']} | "
                f"acerto {pct(m['taxa_acerto_pct'], 1)} | média por operação {pct(m['expectativa_pct'], 3, sign=True)} | drawdown {pct(m['drawdown_max_pct'], 2)}"
            )
            if rep.backtest_expectancy_pct is not None:
                weak = " [yellow](amostra pequena demais para valer como referência)[/]" if rep.backtest_trades < pc.operacoes_min_conclusao else ""
                console.print(
                    f"Backtest fora da amostra: {pct(rep.backtest_expectancy_pct, 3, sign=True)} por operação, em {rep.backtest_trades} operação(ões){weak}."
                )
            if rep.open_trade:
                o = rep.open_trade
                console.print(f"Posição virtual aberta: entrada {num(o['entry_price'])} | stop {num(o['stop'])} | alvo {num(o['target1'])} | desde {format_local(o['entry_ms'], cfg.fuso.local)}")
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
    finally:
        conn.close()
    return 0

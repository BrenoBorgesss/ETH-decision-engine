"""Comandos `analisar`, `sinais listar` e `sinais associar`, e a impressão da decisão."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from rich.markup import escape
from rich.table import Table

from cryptoagent.account.binance_account import split_symbol
from cryptoagent.cli._shared import console
from cryptoagent.config import Config, Secrets
from cryptoagent.data.rest import BinanceReadOnlyClient
from cryptoagent.storage import agent_db
from cryptoagent.timeutils import format_local

if TYPE_CHECKING:  # só para as anotações: importar de verdade aqui deixaria a inicialização mais lenta
    from cryptoagent.engine.live import LiveResult

# -------------------------------------------------------- motor de decisão

ACTION_STYLE = {"COMPRAR": "bold white on green", "VENDER": "bold white on red", "ESPERAR": "bold black on yellow"}


def print_decision(res: LiveResult, cfg: Config) -> None:  # noqa: C901 - só monta texto: um `if` por bloco opcional da tela
    """Imprime no terminal a decisão completa: ação, confiança, parcelas, motivos, travas, níveis e saúde dos dados."""
    from cryptoagent.fmt import brl, num, pct

    d = res.decision
    ratio = res.ratio
    tz = cfg.fuso.local
    tq = split_symbol(d.trade_symbol)[1]

    def tp(x: float) -> str:
        return num(x * ratio, 2) + f" {tq}"

    console.print()
    from cryptoagent.engine.decision import REGIME_PT as ENGINE_REGIME_PT

    console.print(
        f"[{ACTION_STYLE[d.action]}]  {d.action}  [/]  confiança [bold]{num(d.confidence, 0)}%[/]  |  {d.trade_symbol} {tp(d.price)}  |  "
        f"regime {ENGINE_REGIME_PT.get(d.regime, d.regime)} ({cfg.estrategia.tf_setup})  |  {format_local(d.time_ms, tz)} (Brasília)"
    )
    if d.symbol != d.trade_symbol:
        console.print(
            f"[dim]Sinais calculados no {d.symbol} (mais líquido); preços traduzidos para o {d.trade_symbol} pela cotação de agora.[/]"
        )
    if d.position_note:
        console.print(f"[cyan]{escape(d.position_note)}[/]")
    if d.blocks:
        console.print("[bold]Por que ESPERAR:[/]" if d.action == "ESPERAR" else "[bold]Travas:[/]")
        for b in d.blocks:
            console.print(f"  • {escape(b)}")
        if d.parts and d.action == "ESPERAR":
            console.print(
                "[dim]Abaixo, a leitura técnica do momento, só para referência: ela não vale como sinal enquanto houver trava.[/]"
            )
    if d.reasons_for:
        console.print("[bold green]A favor:[/]")
        for r in d.reasons_for[:5]:
            console.print(f"  + {escape(r)}")
    if d.reasons_against:
        console.print("[bold red]Contra:[/]")
        for r in d.reasons_against[:5]:
            console.print(f"  − {escape(r)}")
    if d.parts:
        t = Table(title="Como a confiança foi calculada (multiplicação das parcelas × 100)")
        for col in ("Parcela", "Valor", "Explicação"):
            t.add_column(col)
        for part in d.parts:
            t.add_row(part.name, num(part.value, 2), escape(part.explanation))
        t.add_row("[bold]= confiança[/]", f"[bold]{num(d.confidence, 0)}%[/]", "")
        console.print(t)
    if d.levels:
        lv = d.levels
        t = Table(
            title=f"Níveis sugeridos em {d.trade_symbol}"
            + ("" if d.action == "COMPRAR" else " (só referência: a decisão não é COMPRAR)")
        )
        for col in ("Nível", "Preço", "Distância"):
            t.add_column(col)
        t.add_row("Entrada", tp(lv.entry), "")
        t.add_row("[red]Stop-loss (obrigatório)[/]", tp(lv.stop), pct((lv.stop / lv.entry - 1) * 100, 2, sign=True))
        t.add_row("Alvo 1", tp(lv.target1), pct(lv.target_distance_pct, 2, sign=True) + f" ({lv.target1_source})")
        t.add_row("Alvo 2", tp(lv.target2), pct((lv.target2 / lv.entry - 1) * 100, 2, sign=True))
        t.add_row(
            "Risco/retorno (já com taxas)",
            num(lv.rr, 2),
            f"ganho {pct(lv.reward_net_pct)} x risco {pct(lv.risk_net_pct)}",
        )
        if lv.size_brl:
            eth_price_brl = lv.entry * ratio * (1 if tq == "BRL" else (res.account.fx_usdtbrl or float("nan")))
            loss_at_stop = lv.size_brl * lv.risk_net_pct / 100
            t.add_row(
                "Tamanho sugerido",
                brl(lv.size_brl),
                f"≈ {num(lv.size_brl / eth_price_brl, 6)} ETH; se o stop for atingido, perde ≈ {brl(loss_at_stop)} (com taxas)",
            )
        console.print(t)
    if d.invalidation and d.action != "VENDER":
        console.print("[bold]O que invalida a leitura:[/] " + "; ".join(d.invalidation))
    if d.action == "COMPRAR":
        console.print(
            "[bold yellow]Lembrete: coloque a ordem de STOP (ou OCO) na Binance logo após comprar. O agente não envia ordens.[/]"
        )
    if res.hour_note:
        console.print(f"[dim]Horário: {escape(res.hour_note)}[/]")
    if res.moment is not None and res.moment.stats:
        from cryptoagent.learning.panorama import as_lines

        for line in as_lines(res.moment):
            console.print(f"[dim]Panorama: {escape(line)}[/]")
    if res.champion:
        console.print(f"[dim]Estratégia campeã do aprendizado contínuo: {escape(res.champion)}[/]")
    ages = ", ".join(f"{k} {num(v, 1)}" for k, v in res.data_ages.items() if v >= 0)
    console.print(
        f"[dim]Idade dos dados (s): {ages} | latência da análise {num(res.latency_ms, 0)} ms | {d.disclaimer}[/]"
    )


async def cmd_analyze(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    """Comando `analisar`: calcula a decisão agora, grava o sinal e imprime tudo. Devolve o código de saída do processo."""
    from cryptoagent.engine.journal import save_signal
    from cryptoagent.engine.live import analyze_live

    conn = agent_db.connect(cfg.path(cfg.conta.banco))
    try:
        async with BinanceReadOnlyClient(cfg.binance, secrets) as client:
            res = await analyze_live(cfg, secrets, client, conn)
        signal_id = save_signal(
            conn,
            res.decision,
            ratio=res.ratio,
            mode="ao_vivo",
            data_ages=res.data_ages,
            latency_ms=res.latency_ms,
            market_extras=res.market_extras,
        )
    finally:
        conn.close()
    print_decision(res, cfg)
    console.print(f"[dim]Registrado como sinal #{signal_id}.[/]")
    return 0


async def cmd_signals_list(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    """Comando `sinais listar`: mostra os últimos sinais ao vivo e as compras associadas. Devolve o código de saída do processo."""
    from cryptoagent.fmt import num

    conn = agent_db.connect(cfg.path(cfg.conta.banco))
    try:
        rows = conn.execute(
            "SELECT * FROM signals WHERE mode = 'ao_vivo' ORDER BY time_ms DESC LIMIT ?", (args.n,)
        ).fetchall()
        trades = conn.execute(
            "SELECT symbol, trade_id, time_ms, price, qty, signal_id, association FROM account_trades WHERE is_buyer = 1 ORDER BY time_ms DESC LIMIT 20"
        ).fetchall()
    finally:
        conn.close()
    t = Table(title="Últimos sinais")
    for col in ("#", "Quando (Brasília)", "Par", "Decisão", "Confiança", "Preço", "Stop", "Alvo 1"):
        t.add_column(col)
    for r in rows:
        t.add_row(
            str(r["id"]),
            format_local(r["time_ms"], cfg.fuso.local),
            r["trade_symbol"],
            r["action"],
            f"{num(r['confidence'], 0)}%",
            num(r["price"]),
            num(r["stop"]),
            num(r["target1"]),
        )
    console.print(t)
    if trades:
        a = Table(title="Suas compras e o sinal associado (confirme ou corrija com: sinais associar)")
        for col in ("Par", "Operação", "Quando", "Preço", "Qtd", "Sinal", "Situação"):
            a.add_column(col)
        for r in trades:
            a.add_row(
                r["symbol"],
                str(r["trade_id"]),
                format_local(r["time_ms"], cfg.fuso.local),
                num(r["price"]),
                f"{r['qty']:g}",
                f"#{r['signal_id']}" if r["signal_id"] else "-",
                r["association"],
            )
        console.print(a)
    return 0


async def cmd_signals_associate(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    """Comando `sinais associar`: liga uma compra a um sinal (ou a nenhum). Devolve o código de saída do processo."""
    from cryptoagent.engine.journal import set_association

    conn = agent_db.connect(cfg.path(cfg.conta.banco))
    try:
        signal_id = None if args.sinal in ("nenhum", "0") else int(args.sinal)
        set_association(conn, args.par.upper(), args.operacao, signal_id)
    finally:
        conn.close()
    console.print(
        f"Compra {args.operacao} associada a {'nenhum sinal' if signal_id is None else f'sinal #{signal_id}'}."
    )
    return 0

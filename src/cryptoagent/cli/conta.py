"""Comandos `conta permissoes` e `conta status`."""

from __future__ import annotations

import argparse

from rich.markup import escape
from rich.table import Table

from cryptoagent.account.binance_account import split_symbol
from cryptoagent.account.permissions import LABELS, check_permissions
from cryptoagent.account.status import AccountMonitor, AccountStatus
from cryptoagent.cli._shared import console
from cryptoagent.cli.sessao import _session_table
from cryptoagent.config import Config, Secrets
from cryptoagent.data.health import check_clock
from cryptoagent.data.rest import BinanceReadOnlyClient
from cryptoagent.storage import agent_db
from cryptoagent.timeutils import format_local

# ------------------------------------------------------------------- conta


async def cmd_account_permissions(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    """Comando `conta permissoes`: mostra as permissões da chave e se ela é somente leitura. Devolve o código de saída do processo."""
    if not secrets.has_binance:
        console.print("[yellow]Chave da Binance ausente no .env (BINANCE_API_KEY e BINANCE_API_SECRET).[/]")
        return 1
    async with BinanceReadOnlyClient(cfg.binance, secrets) as client:
        await client.sync_time()
        perms = await check_permissions(client)
    t = Table(title="Permissões da chave da Binance")
    t.add_column("Permissão")
    t.add_column("Ligada?")
    t.add_column("Situação")
    for key, value in sorted(perms.flags.items()):
        if key == "ipRestrict":
            t.add_row(
                "restrição por IP",
                "sim" if value else "não",
                "[green]bom[/]" if value else "[yellow]recomendado ligar[/]",
            )
            continue
        bad = key in perms.forbidden
        t.add_row(LABELS.get(key, key), "sim" if value else "não", "[red]PROIBIDA[/]" if bad else "[green]ok[/]")
    console.print(t)
    perms.raise_if_unsafe()  # se houver permissão demais, o main mostra o erro e sai com código 3
    if not perms.can_read:
        console.print("[red]A chave não tem permissão de leitura.[/]")
        return 1
    console.print("[bold green]Chave é SOMENTE leitura. Ok para usar.[/]")
    return 0


def _print_account(st: AccountStatus, cfg: Config) -> None:
    tz = cfg.fuso.local
    console.print(
        f"Modo da conta: [bold]{st.mode}[/] ({'verificado pela Binance' if st.verified else 'NÃO verificado'})"
    )
    if st.session:
        console.print(_session_table(st.session, cfg))
    if st.balances is not None and st.symbol:
        base, quote = split_symbol(st.symbol)
        t = Table(title=f"Saldo (lido em {format_local(st.balances_ms, tz)})")
        for col in ("Ativo", "Livre", "Em ordens"):
            t.add_column(col)
        for asset in dict.fromkeys([base, quote, "BRL", "USDT", "BNB"]):
            b = st.balances.get(asset)
            if b:
                t.add_row(asset, f"{b.free:,.8f}".rstrip("0").rstrip("."), f"{b.locked:,.8f}".rstrip("0").rstrip("."))
        console.print(t)
        console.print("[dim]Só a posição da operação entra no resultado; o resto do ETH da carteira é ignorado.[/]")
    if st.commission:
        c = st.commission
        origem = "lidas da sua conta" if c.from_account else "do config (leitura falhou)"
        bnb = (
            f"desconto BNB ligado (paga {c.bnb_discount_factor * 100:.0f}% da taxa)"
            if c.bnb_discount_enabled
            else "desconto BNB desligado"
        )
        console.print(
            f"Taxas {origem}: maker {c.maker * 100:.4f}% | taker {c.taker * 100:.4f}% | {bnb} | taxa usada para avaliar a saída: {st.exit_fee_rate * 100:.4f}%"
        )
    if st.day and st.symbol:
        d = st.day
        base, quote = split_symbol(st.symbol)
        to_brl = st.fx_usdtbrl if quote == "USDT" else 1.0
        t = Table(title=f"Operação de hoje em {st.symbol}")
        t.add_column("Item")
        t.add_column("Valor")
        t.add_row(
            "Posição de operação aberta",
            f"{d.open_qty:.6f} {base}"
            + (f" (custo médio real {d.avg_cost_real:,.2f} {quote})" if d.avg_cost_real else ""),
        )
        if d.carried_qty > 1e-12:
            t.add_row("Veio de ontem", f"{d.carried_qty:.6f} {base}, reavaliado a {d.day_start_price:,.2f} (00:00)")
        t.add_row("Operações hoje", str(d.trades_today))
        t.add_row("Realizado hoje", f"{d.realized_quote:+,.2f} {quote}")
        t.add_row(
            "Posição aberta (saída agora)",
            f"{d.unrealized_quote:+,.2f} {quote} (bid {d.mark_price:,.2f}, já com taxa de venda)",
        )
        t.add_row("Taxas pagas hoje", f"{d.fees_quote:,.2f} {quote}")
        if quote == "USDT":
            t.add_row("Câmbio usado", f"USDTBRL {to_brl:,.4f}")
        if st.limits:
            t.add_row("[bold]Resultado do dia[/]", f"[bold]R$ {st.limits.pnl_brl:+,.2f} ({st.limits.pnl_pct:+.2f}%)[/]")
        console.print(t)
    if st.limits:
        lim = st.limits
        bar = "█" * int(lim.used_pct / 5) + "░" * (20 - int(lim.used_pct / 5))
        console.print(
            f"Perda máxima: R$ {lim.max_loss_brl:,.2f} | usada R$ {lim.loss_used_brl:,.2f} [{bar}] {lim.used_pct:.0f}% | "
            f"resta R$ {lim.remaining_brl:,.2f} | risco por operação R$ {lim.risk_per_trade_brl:,.2f} | capital base R$ {lim.capital_brl:,.2f}"
        )
    for w in st.warnings:
        console.print(f"[yellow]aviso: {escape(w)}[/]")
    if st.blocked:
        console.print("[bold red]Sinal bloqueado: ESPERAR.[/]")
        for r in st.reasons:
            console.print(f"  • {escape(r)}")
    else:
        console.print(
            "[bold green]Conta em ordem: limites permitem sinais.[/] (A decisão em si: cryptoagent analisar.)"
        )


async def cmd_account_status(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    """Comando `conta status`: mostra saldo, taxas, resultado do dia e limites. Devolve o código de saída do processo."""
    conn = agent_db.connect(cfg.path(cfg.conta.banco))
    try:
        async with BinanceReadOnlyClient(cfg.binance, secrets) as client:
            offset, _ = await client.sync_time()
            clock = check_clock(offset, cfg.saude_dados.desvio_relogio_max_ms)
            st = await AccountMonitor(cfg, secrets, client, conn).refresh()
            if not clock.ok:
                st.reasons.append(clock.detail)
    finally:
        conn.close()
    _print_account(st, cfg)
    return 2 if st.blocked else 0

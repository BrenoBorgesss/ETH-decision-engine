"""Peças usadas por vários comandos: console, log, tabela de saúde dos dados e leitura das taxas da conta."""

from __future__ import annotations

import logging

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from cryptoagent.account.permissions import check_permissions
from cryptoagent.config import Config, Secrets
from cryptoagent.data.health import HealthReport
from cryptoagent.data.rest import BinanceError, BinanceReadOnlyClient
from cryptoagent.security.redact import redact

log = logging.getLogger("cryptoagent")
console = Console()


# --------------------------------------------------------------------- saída


def _print_health(report: HealthReport) -> None:
    table = Table(title="Saúde dos dados", show_lines=False)
    table.add_column("Verificação")
    table.add_column("Status")
    table.add_column("Detalhe")
    table.add_column("Fonte", style="dim")
    for c in report.checks:
        status = "[green]OK[/]" if c.ok else ("[red]PROBLEMA[/]" if c.critical else "[yellow]AVISO[/]")
        table.add_row(c.name, status, c.detail, c.source or "")
    console.print(table)
    if report.ok:
        console.print("[bold green]Dados em ordem.[/] (Para a decisão completa: cryptoagent analisar ou cryptoagent painel.)")
    else:
        console.print("[bold red]Há problemas nos dados: qualquer sinal agora seria ESPERAR.[/]")
        for p in report.problems:
            console.print(f"  • {p}")
    for w in report.warnings:
        console.print(f"[yellow]  aviso: {w}[/]")


async def _account_taker_fees(cfg: Config, secrets: Secrets, symbols: list[str]) -> dict[str, tuple[float, str]]:
    """Taxa taker efetiva de cada par, lida da conta (somente leitura). Vazio se não houver chave ou a leitura falhar."""
    from cryptoagent.account.binance_account import fetch_balances, fetch_commission

    if not secrets.has_binance:
        return {}
    out: dict[str, tuple[float, str]] = {}
    try:
        async with BinanceReadOnlyClient(cfg.binance, secrets) as client:
            await client.sync_time()
            (await check_permissions(client)).raise_if_unsafe()
            balances = (await fetch_balances(client)).value
            has_bnb = "BNB" in balances and balances["BNB"].total > 0
            for sym in symbols:
                info = (await fetch_commission(client, sym)).value
                note = "com desconto BNB" if info.bnb_discount_enabled and has_bnb else "sem desconto BNB"
                out[sym] = (info.effective("taker", has_bnb=has_bnb), f"lida da sua conta ({note})")
    except BinanceError as exc:
        console.print(f"[yellow]Não foi possível ler as taxas da conta ({escape(redact(exc))}); usando as do config.[/]")
        return {}
    return out

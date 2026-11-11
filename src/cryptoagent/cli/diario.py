"""Comandos `diario adicionar`, `diario listar` e `diario remover` (diário manual)."""

from __future__ import annotations

import argparse
import sqlite3

from rich.table import Table

from cryptoagent.account import journal
from cryptoagent.account.binance_account import split_symbol
from cryptoagent.cli._shared import console
from cryptoagent.config import Config, Secrets
from cryptoagent.session import daily
from cryptoagent.storage import agent_db
from cryptoagent.timeutils import format_local

# ------------------------------------------------------------ diário manual


def _journal_symbol(cfg: Config, conn: sqlite3.Connection, override: str | None) -> str:
    if override:
        return override.upper()
    session = daily.get_session(conn, daily.today(cfg.fuso.local, cfg.fuso.inicio_dia_local))
    if session:
        return session.par_operacao
    if cfg.mercado.par_operacao:
        return cfg.mercado.par_operacao
    raise ValueError("Informe o par com --par (ou responda a sessão do dia antes).")


async def cmd_journal_add(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    """Comando `diario adicionar`: registra no diário manual uma operação feita pelo usuário. Devolve o código de saída do processo."""
    conn = agent_db.connect(cfg.path(cfg.conta.banco))
    try:
        symbol = _journal_symbol(cfg, conn, args.par)
        side = {"compra": "buy", "venda": "sell"}[args.lado]
        fee = args.taxa if args.taxa is not None else args.qtd * args.preco * cfg.custos.taxa_padrao_taker
        trade_id = journal.add_manual_trade(
            conn, symbol=symbol, side=side, qty=args.qtd, price=args.preco, fee_quote=fee, note=args.nota or ""
        )
    finally:
        conn.close()
    _, quote = split_symbol(symbol)
    console.print(
        f"Registrado #{trade_id}: {args.lado} de {args.qtd} a {args.preco:,.2f} {quote} em {symbol} (taxa {fee:,.4f} {quote})."
    )
    if cfg.conta.modo != "diario_manual":
        console.print("[yellow]Obs.: o diário só é usado com conta.modo: diario_manual no config.[/]")
    return 0


async def cmd_journal_list(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    """Comando `diario listar`: lista o diário manual do par. Devolve o código de saída do processo."""
    conn = agent_db.connect(cfg.path(cfg.conta.banco))
    try:
        symbol = _journal_symbol(cfg, conn, args.par)
        rows = journal.list_manual_trades(conn, symbol)
    finally:
        conn.close()
    t = Table(title=f"Diário manual: {symbol} (NÃO verificado)")
    for col in ("#", "Quando (Brasília)", "Lado", "Qtd", "Preço", "Taxa", "Nota"):
        t.add_column(col)
    for r in rows:
        t.add_row(
            str(r["id"]),
            format_local(r["time_ms"], cfg.fuso.local),
            "compra" if r["side"] == "buy" else "venda",
            f"{r['qty']:g}",
            f"{r['price']:,.2f}",
            f"{r['fee_quote']:,.4f}",
            r["note"] or "",
        )
    console.print(t)
    return 0


async def cmd_journal_remove(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    """Comando `diario remover`: apaga um registro do diário manual. Devolve o código de saída do processo."""
    conn = agent_db.connect(cfg.path(cfg.conta.banco))
    try:
        ok = journal.remove_manual_trade(conn, args.id)
    finally:
        conn.close()
    console.print(f"Registro #{args.id} removido." if ok else f"[yellow]Registro #{args.id} não encontrado.[/]")
    return 0 if ok else 1

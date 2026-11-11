"""Comandos `sessao iniciar` e `sessao ver` (perguntas do início do dia)."""

from __future__ import annotations

import argparse

from rich.markup import escape
from rich.prompt import Confirm, FloatPrompt, Prompt
from rich.table import Table

from cryptoagent.cli._shared import console
from cryptoagent.config import Config, Secrets
from cryptoagent.session import daily
from cryptoagent.storage import agent_db
from cryptoagent.timeutils import format_local

# ------------------------------------------------------------ sessão do dia


def _session_table(s: daily.DailySession, cfg: Config) -> Table:
    t = Table(title=f"Sessão do dia {s.day.strftime('%d/%m/%Y')} (horário de Brasília)")
    t.add_column("Campo")
    t.add_column("Valor")
    t.add_row("Capital de hoje", f"R$ {s.capital_brl:,.2f}")
    t.add_row(
        "Perda máxima de hoje", f"R$ {s.max_loss_brl:,.2f} ({s.max_loss_brl / s.capital_brl * 100:.1f}% do capital)"
    )
    t.add_row(
        "Risco por operação",
        f"R$ {s.max_loss_brl / cfg.risco.divisor_perda_diaria:,.2f} (perda máxima ÷ {cfg.risco.divisor_perda_diaria:g})",
    )
    t.add_row("Par da operação", s.par_operacao)
    t.add_row("Saldo mantido em", s.saldo_em)
    t.add_row("Atualizada em", format_local(s.updated_ms, cfg.fuso.local))
    return t


async def cmd_session_start(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    """Comando `sessao iniciar`: pergunta (ou recebe por argumento) o capital e a perda máxima de hoje e grava a sessão. Devolve o código de saída do processo."""
    conn = agent_db.connect(cfg.path(cfg.conta.banco))
    try:
        day = daily.today(cfg.fuso.local, cfg.fuso.inicio_dia_local)
        existing = daily.get_session(conn, day)
        last = existing or daily.last_session(conn)
        candidates = cfg.mercado.pares_candidatos
        interactive = args.capital is None or args.perda_max is None

        if existing and interactive:
            console.print(_session_table(existing, cfg))
            if not Confirm.ask("A sessão de hoje já existe. Quer alterar?", default=False):
                return 0

        def ask_float(question: str, default: float | None) -> float:
            return FloatPrompt.ask(question, default=default) if default is not None else FloatPrompt.ask(question)

        default_capital = (
            last.capital_brl if last else (cfg.capital_operacao.valor if cfg.capital_operacao.modo == "brl" else None)
        )
        capital = (
            args.capital
            if args.capital is not None
            else ask_float("(1) Com quantos R$ você quer começar hoje?", default_capital)
        )
        max_loss = (
            args.perda_max
            if args.perda_max is not None
            else ask_float(
                "(2) Qual o MÁXIMO que você aceita perder hoje, em R$?", existing.max_loss_brl if existing else None
            )
        )
        par = (args.par or (existing.par_operacao if existing else None) or cfg.mercado.par_operacao or "").upper()
        if not par:
            par = Prompt.ask(
                "Em qual par você vai operar hoje?",
                choices=candidates,
                default=last.par_operacao if last else candidates[0],
            )
        if par.endswith("BRL"):
            saldo = "BRL"
        else:
            saldo = (args.saldo or "").upper() or Prompt.ask(
                "Você mantém o saldo da operação em USDT ou em BRL?",
                choices=["USDT", "BRL"],
                default=last.saldo_em if last else "USDT",
            )
        session = daily.save_session(
            conn,
            day,
            capital_brl=capital,
            max_loss_brl=max_loss,
            par_operacao=par,
            saldo_em=saldo,
            candidates=candidates,
        )
    except daily.SessionError as exc:
        console.print(f"[red]Sessão não salva:[/] {escape(str(exc))}")
        return 1
    finally:
        conn.close()
    console.print(_session_table(session, cfg))
    if session.max_loss_brl > session.capital_brl * cfg.risco.aviso_perda_max_pct_capital / 100:
        console.print(
            f"[yellow]Atenção: a perda máxima passa de {cfg.risco.aviso_perda_max_pct_capital:g}% do capital.[/]"
        )
    return 0


async def cmd_session_show(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    """Comando `sessao ver`: mostra a sessão de hoje. Devolve o código de saída do processo."""
    conn = agent_db.connect(cfg.path(cfg.conta.banco))
    try:
        session = daily.get_session(conn, daily.today(cfg.fuso.local, cfg.fuso.inicio_dia_local))
    finally:
        conn.close()
    if session is None:
        console.print(
            "[yellow]Sessão de hoje ainda não respondida: o agente fica em ESPERAR. Rode: cryptoagent sessao iniciar[/]"
        )
        return 1
    console.print(_session_table(session, cfg))
    return 0

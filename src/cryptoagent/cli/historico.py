"""Comandos `historico baixar` e `historico verificar`."""

from __future__ import annotations

import argparse

import httpx
from rich.table import Table

from cryptoagent.cli._shared import console
from cryptoagent.config import Config, Secrets
from cryptoagent.data import history
from cryptoagent.data.rest import BinanceReadOnlyClient
from cryptoagent.timeutils import format_local

# --------------------------------------------------------------- histórico


def _history_plan(cfg: Config, symbols: list[str] | None) -> dict[str, list[str]]:
    plan = cfg.historico.intervalos
    if symbols:
        wanted = [s.upper() for s in symbols]
        unknown = [s for s in wanted if s not in plan]
        if unknown:
            raise ValueError(f"Par(es) sem intervalos no config (historico.intervalos): {', '.join(unknown)}")
        plan = {s: plan[s] for s in wanted}
    return plan


async def cmd_history_download(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    store = history.HistoryStore(cfg.path(cfg.historico.pasta))
    months = args.meses or cfg.historico.meses
    plan = _history_plan(cfg, args.simbolos)
    table = Table(title=f"Histórico ({months} meses + mês atual)")
    for col in ("Par", "Intervalo", "Arquivos novos", "Já tinha", "Via REST", "Linhas", "De (UTC)", "Até (UTC)", "Candles faltando", "Problemas"):
        table.add_column(col)
    h = cfg.historico
    async with httpx.AsyncClient(timeout=h.timeout_download_s, follow_redirects=True) as http, BinanceReadOnlyClient(cfg.binance, secrets) as client:
        await client.sync_time()
        for symbol, intervals in plan.items():
            for interval in intervals:
                console.print(f"Baixando {symbol} {interval}...")
                stats = await history.download_history(
                    store, http, cfg.binance.dados_historicos_url, symbol, interval, months, concurrency=h.downloads_simultaneos,
                    attempts=h.tentativas_download, retry_wait_s=h.espera_entre_tentativas_s,
                )
                stats.rows_from_rest = await history.update_from_rest(
                    store, client, symbol, interval, months=months, now=client.server_now_ms()
                )
                final = history.summarize(store, symbol, interval)
                problems = []
                if stats.missing:
                    problems.append(f"sem arquivo: {', '.join(stats.missing[:3])}{'...' if len(stats.missing) > 3 else ''}")
                if stats.checksum_failures:
                    problems.append(f"checksum falhou: {', '.join(stats.checksum_failures)}")
                table.add_row(
                    symbol,
                    interval,
                    str(stats.files_ok),
                    str(stats.files_skipped),
                    str(stats.rows_from_rest),
                    f"{final.rows_total:,}",
                    format_local(final.first_ms, "UTC", "%Y-%m-%d %H:%M"),
                    format_local(final.last_ms, "UTC", "%Y-%m-%d %H:%M"),
                    f"{final.missing_candles:,} em {len(final.gaps)} buraco(s)",
                    "; ".join(problems) or "-",
                )
    console.print(table)
    console.print(f"Arquivos em: {store.folder}")
    return 0


async def cmd_history_check(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    store = history.HistoryStore(cfg.path(cfg.historico.pasta))
    table = Table(title="Cobertura do histórico")
    for col in ("Par", "Intervalo", "Linhas", "De (UTC)", "Até (UTC)", "Buracos", "Maiores buracos (UTC)"):
        table.add_column(col)
    for symbol, intervals in _history_plan(cfg, args.simbolos).items():
        for interval in intervals:
            s = history.summarize(store, symbol, interval)
            biggest = sorted(s.gaps, key=lambda g: g.missing, reverse=True)[:3]
            table.add_row(
                symbol,
                interval,
                f"{s.rows_total:,}",
                format_local(s.first_ms, "UTC", "%Y-%m-%d %H:%M"),
                format_local(s.last_ms, "UTC", "%Y-%m-%d %H:%M"),
                f"{s.missing_candles:,} candles em {len(s.gaps)}",
                ", ".join(f"{format_local(g.start_ms, 'UTC', '%Y-%m-%d %H:%M')} ({g.missing})" for g in biggest) or "-",
            )
    console.print(table)
    return 0

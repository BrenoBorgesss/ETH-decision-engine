"""Comandos `historico baixar` e `historico verificar`."""

from __future__ import annotations

import argparse

import httpx
from rich.table import Table

from cryptoagent.cli._shared import console
from cryptoagent.config import Config, Secrets
from cryptoagent.data import history
from cryptoagent.data.rest import BinanceReadOnlyClient
from cryptoagent.data.retry import RetryPolicy
from cryptoagent.fmt import num
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
    """Comando `historico baixar`: baixa ou atualiza o histórico de candles e mostra a cobertura. Devolve o código de saída do processo."""
    store = history.HistoryStore(cfg.path(cfg.historico.pasta))
    months = args.meses or cfg.historico.meses
    plan = _history_plan(cfg, args.simbolos)
    table = Table(title=f"Histórico ({months} meses + mês atual)")
    for col in ("Par", "Intervalo", "Arquivos novos", "Já tinha", "Via REST", "Linhas", "De (UTC)", "Até (UTC)", "Candles faltando", "Problemas"):
        table.add_column(col)
    h = cfg.historico
    retry = RetryPolicy(
        attempts=h.tentativas_download, base_s=h.espera_entre_tentativas_s, max_s=h.espera_max_s,
        retry_after_default_s=cfg.binance.espera_429_padrao_s, retry_after_max_s=cfg.binance.espera_429_max_s,
    )
    async with httpx.AsyncClient(timeout=h.timeout_download_s, follow_redirects=True) as http, BinanceReadOnlyClient(cfg.binance, secrets) as client:
        await client.sync_time()
        for symbol, intervals in plan.items():
            for interval in intervals:
                console.print(f"Baixando {symbol} {interval}...")
                stats = await history.download_history(
                    store, http, cfg.binance.dados_historicos_url, symbol, interval, months, concurrency=h.downloads_simultaneos, retry=retry, outlier_sigmas=h.outlier_desvios,
                )
                stats.rows_from_rest = await history.update_from_rest(
                    store, client, symbol, interval, months=months, now=client.server_now_ms(), max_requests=h.max_consultas_rest
                )
                final = history.summarize(store, symbol, interval, h.outlier_desvios)
                problems = []
                if stats.missing:
                    problems.append(f"sem arquivo: {', '.join(stats.missing[:3])}{'...' if len(stats.missing) > 3 else ''}")
                if stats.checksum_failures:
                    problems.append(f"checksum falhou: {', '.join(stats.checksum_failures)}")
                if stats.invalid_rows:
                    problems.append(f"{stats.invalid_rows} linha(s) inválida(s) descartada(s)")
                table.add_row(
                    symbol,
                    interval,
                    str(stats.files_ok),
                    str(stats.files_skipped),
                    str(stats.rows_from_rest),
                    num(final.rows_total, 0),
                    format_local(final.first_ms, "UTC", "%Y-%m-%d %H:%M"),
                    format_local(final.last_ms, "UTC", "%Y-%m-%d %H:%M"),
                    f"{num(final.missing_candles, 0)} em {len(final.gaps)} buraco(s)",
                    "; ".join(problems) or "-",
                )
    console.print(table)
    console.print(f"Arquivos em: {store.folder}")
    return 0


async def cmd_history_check(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    """Comando `historico verificar`: mostra a cobertura e os buracos do histórico já baixado. Devolve o código de saída do processo."""
    store = history.HistoryStore(cfg.path(cfg.historico.pasta))
    table = Table(title="Cobertura e qualidade do histórico")
    for col in ("Par", "Intervalo", "Linhas", "De (UTC)", "Até (UTC)", "Inválidos", "Outliers", "Buracos", "Maiores buracos (UTC)", "Atraso"):
        table.add_column(col)
    all_stats = [
        history.summarize(store, symbol, interval, cfg.historico.outlier_desvios)
        for symbol, intervals in _history_plan(cfg, args.simbolos).items()
        for interval in intervals
    ]
    lag = history.series_lag(all_stats)
    invalid_total = 0
    for s in all_stats:
        biggest = sorted(s.gaps, key=lambda g: g.missing, reverse=True)[:3]
        q = s.quality
        invalid_total += q.invalid_rows + q.duplicates + q.out_of_order if q else 0
        behind = lag.get((s.symbol, s.interval), 0)
        table.add_row(
            s.symbol,
            s.interval,
            num(s.rows_total, 0),
            format_local(s.first_ms, "UTC", "%Y-%m-%d %H:%M"),
            format_local(s.last_ms, "UTC", "%Y-%m-%d %H:%M"),
            (f"[red]{q.invalid_rows + q.duplicates + q.out_of_order}[/]" if q.invalid_rows + q.duplicates + q.out_of_order else "0") if q else "-",
            str(len(q.outliers)) if q else "-",
            f"{num(s.missing_candles, 0)} candles em {len(s.gaps)}",
            ", ".join(f"{format_local(g.start_ms, 'UTC', '%Y-%m-%d %H:%M')} ({g.missing})" for g in biggest) or "-",
            f"[yellow]{num(behind, 0)} candle(s)[/]" if behind else "-",
        )
    console.print(table)
    console.print(
        "[dim]Inválidos: valor ausente, horário fora da grade, preço incoerente, volume negativo, duplicado ou fora de ordem. "
        f"Outliers: retorno a mais de {cfg.historico.outlier_desvios:g} desvios robustos da mediana (só relatados; podem ser movimentos reais). "
        "Atraso: candles que faltam para a série alcançar a mais recente.[/]"
    )
    if lag:
        console.print("[yellow]As séries terminam em momentos diferentes. Para alinhar: cryptoagent historico baixar[/]")
    if invalid_total:
        console.print(f"[red]{invalid_total} linha(s) com problema no histórico gravado. Baixe de novo: apague o arquivo do par/intervalo e rode cryptoagent historico baixar[/]")
        return 2
    return 0

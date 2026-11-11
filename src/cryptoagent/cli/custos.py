"""Comandos `custos coletar` e `custos resumo`."""

from __future__ import annotations

import argparse
import asyncio
import sqlite3

from rich.table import Table

from cryptoagent.cli._shared import console
from cryptoagent.config import Config, Secrets
from cryptoagent.costs.recorder import CostRecorder
from cryptoagent.costs.summary import collection_summary
from cryptoagent.data.health import check_clock
from cryptoagent.data.rest import BinanceReadOnlyClient
from cryptoagent.storage import db
from cryptoagent.timeutils import format_local

# ------------------------------------------------------------------ custos


async def cmd_costs_collect(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    """Comando `custos coletar`: grava spread, profundidade e slippage ao vivo até ser interrompido. Devolve o código de saída do processo."""
    conn = db.connect(cfg.path(cfg.custos.banco))
    console.print(
        f"Coletando custos de {', '.join(cfg.mercado.simbolos_monitorados)} a cada {cfg.custos.amostra_a_cada_s:g} s "
        f"em {cfg.path(cfg.custos.banco)}.\nDeixe rodando (mínimo {cfg.custos.dias_minimos_para_conclusao} dias, ideal 14). "
        "Ctrl+C para parar; pode reiniciar depois que os dados continuam."
    )
    try:
        async with BinanceReadOnlyClient(cfg.binance, secrets) as client:
            offset, _ = await client.sync_time()
            clock = check_clock(offset, cfg.saude_dados.desvio_relogio_max_ms)
            if not clock.ok:
                console.print(f"[yellow]Aviso: {clock.detail}[/]")
            recorder = CostRecorder(cfg, client, conn, console=console)
            await recorder.run(asyncio.Event(), duration_s=args.segundos)
            console.print(f"Encerrado. Amostras gravadas nesta execução: {recorder.samples_written}")
    finally:
        conn.close()
    return 0


async def cmd_costs_summary(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    """Comando `custos resumo`: mostra quantos dias e amostras já foram coletados. Devolve o código de saída do processo."""
    path = cfg.path(cfg.custos.banco)
    if not path.is_file():
        console.print("Ainda não há coleta de custos. Rode: cryptoagent custos coletar")
        return 1
    conn = sqlite3.connect(path)
    try:
        rows = collection_summary(conn, cfg.fuso.local, cfg.fuso.inicio_dia_local, cfg.custos.amostra_a_cada_s)
    finally:
        conn.close()
    tz = cfg.fuso.local
    table = Table(title="Coleta de custos (order book ao vivo)")
    for col in (
        "Par",
        "Amostras",
        "De",
        "Até (Brasília)",
        "Dias",
        "Fins de semana",
        "Cobertura",
        "Via REST",
        "Spread médio",
        "Mediano",
        "P95",
    ):
        table.add_column(col)
    for r in rows:
        table.add_row(
            r.symbol,
            f"{r.samples:,}",
            format_local(r.first_ms, tz, "%d/%m %H:%M"),
            format_local(r.last_ms, tz, "%d/%m %H:%M"),
            str(r.local_days),
            str(r.weekend_days),
            f"{r.coverage_pct:.0f}%",
            f"{r.rest_share_pct:.1f}%",
            f"{r.spread_mean_pct:.4f}%",
            f"{r.spread_median_pct:.4f}%",
            f"{r.spread_p95_pct:.4f}%",
        )
    console.print(table)
    days = min((r.local_days for r in rows), default=0)
    need = cfg.custos.dias_minimos_para_conclusao
    if days < need:
        console.print(
            f"[yellow]Só {days} dia(s) de coleta (mínimo {need}). Ainda NÃO dá para tirar conclusões sobre custos.[/]"
        )
    return 0

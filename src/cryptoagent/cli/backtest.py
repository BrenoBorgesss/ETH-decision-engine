"""Comando `backtest`."""

from __future__ import annotations

import argparse
from pathlib import Path

from rich.table import Table

from cryptoagent.cli._shared import _account_taker_fees, console
from cryptoagent.config import Config, Secrets
from cryptoagent.timeutils import format_local

# ----------------------------------------------------------------- backtest


async def cmd_backtest(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    from cryptoagent.backtest.costs import build_schedule, load_schedules, save_schedules
    from cryptoagent.backtest.data import load_backtest_data
    from cryptoagent.backtest.report import COLS, _fmt, render_html, save_json
    from cryptoagent.backtest.runner import run_pair

    bt = cfg.backtest
    pairs = [x.upper() for x in args.pares] if args.pares else bt.pares
    # Custos congelados: se o arquivo existir, a conta e o banco de custos nem são lidos.
    frozen_path: Path | None = Path(args.custos_arquivo) if args.custos_arquivo else None
    frozen = load_schedules(frozen_path) if frozen_path is not None and frozen_path.is_file() else None
    if frozen is not None:
        missing = [p for p in pairs if p not in frozen]
        if missing:
            raise ValueError(f"o arquivo de custos {frozen_path} não tem o(s) par(es): {', '.join(missing)}")
        console.print(f"[dim]Custos lidos de {frozen_path} (congelados): a conta e o banco de custos não foram consultados.[/]")
    fees = {} if frozen is not None else await _account_taker_fees(cfg, secrets, pairs)
    reports = []
    used = []
    for pair in pairs:
        if frozen is not None:
            costs = frozen[pair]
        else:
            fee, fee_source = fees.get(pair, (cfg.custos.taxa_padrao_taker, "do config (sem leitura da conta)"))
            costs = build_schedule(
                pair, fee, cfg.sazonalidade.spread_padrao_pct.get(pair, 0.05), cfg.path(cfg.custos.banco), cfg.fuso.local,
                min_samples=bt.amostras_min_spread_hora, slip_market=bt.slippage_mercado_pct, slip_stop=bt.slippage_stop_pct, fee_source=fee_source,
            )
        used.append(costs)
        console.print(f"[bold]{pair}[/]: carregando histórico e preparando indicadores...")
        data = load_backtest_data(cfg, pair, season_cost_pct=costs.mean_roundtrip_pct)
        rep = run_pair(cfg, data, costs, quick=args.rapido, progress=lambda msg: console.print(f"[dim]  {msg}[/]"))
        reports.append(rep)

        t = Table(title=f"{pair}: fora da amostra ({format_local(rep.oos_start, cfg.fuso.local, '%d/%m/%Y')} a {format_local(rep.oos_end, cfg.fuso.local, '%d/%m/%Y')}), já com taxas e slippage")
        t.add_column("Variante")
        for _, title, _ in COLS:
            t.add_column(title)
        rows = [rep.default, rep.walkforward, rep.naive, *rep.distances, *rep.ablation[1:]]
        for v in rows:
            if v is not None:
                t.add_row(v.name, *(_fmt(k, v.metrics.get(k)) for k, _, _ in COLS))
        console.print(t)
        bh = rep.buy_hold or {}
        console.print(f"Comprar e segurar no mesmo período: {_fmt('retorno_total_pct', bh.get('retorno_total_pct'))} (drawdown {_fmt('drawdown_max_pct', bh.get('drawdown_max_pct'))})")
        console.print(f"[dim]{pair} levou {rep.elapsed_s:.0f} s[/]")

    if frozen_path is not None and frozen is None:
        save_schedules(frozen_path, used)
        console.print(f"[dim]Custos desta execução gravados em {frozen_path}; use o mesmo arquivo para repetir o backtest.[/]")
    folder = cfg.path(bt.relatorio_pasta)
    save_json(reports, folder / "backtest.json")
    render_html(reports, folder / "backtest.html", capital=bt.capital, tz=cfg.fuso.local, train_months=bt.meses_treino, default_distance=cfg.estrategia.distancia_alvo_min_custo)
    console.print(f"\nRelatório completo (com ablação, walk-forward mês a mês e motivos): [bold]{folder / 'backtest.html'}[/]")
    console.print("[dim]Resultados passados não garantem resultados futuros.[/]")
    return 0

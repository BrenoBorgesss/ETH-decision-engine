"""Comandos `sazonalidade analisar` e `sazonalidade agora` (análise por horário)."""

from __future__ import annotations

import argparse
from pathlib import Path

from rich.table import Table

from cryptoagent.cli._shared import _account_taker_fees, console
from cryptoagent.config import Config, Secrets
from cryptoagent.data import history
from cryptoagent.data.rest import BinanceReadOnlyClient
from cryptoagent.timeutils import format_local, local_trading_day_series

# ------------------------------------------------------- análise por horário

CLASS_PT = {"compensa": "[green]compensa[/]", "neutro": "[yellow]neutro[/]", "nao_compensa": "[red]não compensa[/]"}


def _season_paths(cfg: Config) -> tuple[Path, Path]:
    folder = cfg.path(cfg.sazonalidade.relatorio_pasta)
    return folder / "sazonalidade.json", folder / "sazonalidade.html"


async def cmd_season_analyze(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    from cryptoagent.seasonality.analysis import analyze_symbol, render_html, save_json
    from cryptoagent.seasonality.cost_model import build_cost_model
    from cryptoagent.seasonality.profiles import WEEKDAYS_PT

    s, tz = cfg.sazonalidade, cfg.fuso.local
    store = history.HistoryStore(cfg.path(cfg.historico.pasta))
    symbols = [x.upper() for x in args.simbolos] if args.simbolos else cfg.mercado.pares_candidatos
    fees = await _account_taker_fees(cfg, secrets, symbols)
    analyses = []
    for sym in symbols:
        df = store.load(sym, s.timeframe)
        if len(df) < 1000:
            console.print(f"[yellow]{sym}: sem histórico de {s.timeframe} suficiente (rode historico baixar).[/]")
            continue
        fee, fee_source = fees.get(sym, (cfg.custos.taxa_padrao_taker, "do config (sem leitura da conta)"))
        console.print(f"[dim]{sym}: taxa taker {fee * 100:.4f}% por lado, {fee_source}.[/]")
        cost = build_cost_model(sym, fee, s.spread_padrao_pct, cfg.path(cfg.custos.banco))
        a = analyze_symbol(df, sym, cfg, cost)
        analyses.append(a)
        v = a.validation

        console.rule(f"{sym} | candles de {s.timeframe}")
        console.print(
            f"Custo de ida e volta: [bold]{cost.breakeven_pct:.3f}%[/] (taxa {cost.fee_pct_side:.4f}%/lado + spread {cost.spread_pct:.4f}%: {cost.spread_source}). "
            f"'Compensa' = amplitude > {cost.breakeven_pct * s.custo_folga:.3f}%."
        )
        t = Table(title=f"Perfil por hora (Brasília), últimos {s.meses_treino} meses")
        for col in ("Hora", "Amplitude mediana", "% acima do custo", "Volume médio", "Classe", "Padrão validado?"):
            t.add_column(col)
        for h, r in a.hours.iterrows():
            t.add_row(f"{int(h):02d}h", f"{r['range_median']:.3f}%", f"{r['pct_above_cost']:.0f}%", f"{r['quote_vol_mean']:,.0f}", CLASS_PT[r["class"]], "[green]sim[/]" if r["validated"] else "[dim]não[/]")
        console.print(t)
        w = Table(title="Por dia da semana")
        for col in ("Dia", "Amplitude mediana", "% acima do custo"):
            w.add_column(col)
        for d, r in a.weekdays.iterrows():
            w.add_row(WEEKDAYS_PT[int(d)], f"{r['range_median']:.3f}%", f"{r['pct_above_cost']:.0f}%")
        console.print(w)
        sp = ", ".join(f"{x:.2f}" for x in v.spearman)
        console.print(
            f"Validação walk-forward: {len(v.spearman)} meses de teste (treino {s.meses_treino} meses → teste {s.meses_teste}). "
            f"Formato do dia se repete fora da amostra? {'[green]sim[/]' if v.profile_repeats else '[red]não[/]'} (Spearman: {sp})"
        )
        for kind, label in (("hora", "horas"), ("dia_semana", "dias da semana"), ("hora_dia", "hora × dia")):
            kept, disc = v.kept(kind), v.discarded(kind)
            console.print(f"  {label}: [green]{len(kept)} mantidos[/], [red]{len(disc)} descartados[/]")
            if kind != "hora_dia":
                for p in kept:
                    console.print(f"    [green]✓[/] {p.key} {p.claim.replace('_', ' ')} (confirmou {p.held}/{p.claims})")
                for p in disc:
                    console.print(f"    [red]✗[/] {p.key} {p.claim.replace('_', ' ')} (confirmou {p.held}/{p.claims})")
            else:
                for p in kept[:15]:
                    console.print(f"    [green]✓[/] {p.key} {p.claim.replace('_', ' ')} (confirmou {p.held}/{p.claims})")
    if not analyses:
        return 1
    json_path, html_path = _season_paths(cfg)
    save_json(analyses, json_path)
    render_html(analyses, html_path, tz)
    console.print(f"\nMapa de calor e lista completa de padrões: [bold]{html_path}[/]\nDados para o agente: {json_path}")
    console.print("[dim]Isto é filtro/contexto: reduz a confiança em horários parados; nunca é motivo sozinho para comprar ou vender.[/]")
    return 0


async def cmd_season_now(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    from cryptoagent.seasonality.analysis import load_profiles
    from cryptoagent.seasonality.compare import candle_vs_same_hour, context_multiplier, day_range_vs_history
    from cryptoagent.seasonality.profiles import add_buckets

    s, tz = cfg.sazonalidade, cfg.fuso.local
    json_path, _ = _season_paths(cfg)
    profiles = load_profiles(json_path)
    sym = (args.simbolo or cfg.mercado.par_operacao or cfg.mercado.simbolo_analise).upper()
    if sym not in profiles:
        console.print(f"[yellow]Sem perfil de {sym}. Rode antes: cryptoagent sazonalidade analisar[/]")
        return 1
    store = history.HistoryStore(cfg.path(cfg.historico.pasta))
    async with BinanceReadOnlyClient(cfg.binance, secrets) as client:
        await client.sync_time()
        await history.update_from_rest(store, client, sym, s.timeframe, months=cfg.historico.meses, now=client.server_now_ms())
    df = store.load(sym, s.timeframe)
    df = df[df["open_time"] >= int(df["open_time"].iloc[-1]) - (s.dias_comparacao + 2) * 86_400_000]
    df_b = add_buckets(df, tz, cfg.indicadores.atr)
    candle = candle_vs_same_hour(df_b, s.dias_comparacao, s.percentil_baixo, s.percentil_alto)
    day = day_range_vs_history(df_b, local_trading_day_series(df_b["open_time"], tz, cfg.fuso.inicio_dia_local), s.dias_comparacao, s.percentil_baixo, s.percentil_alto)
    hour = int(df_b["hour"].iloc[-1])
    prof = profiles[sym]
    hrow = next(h for h in prof["hours"] if h["hour"] == hour)
    ctx = context_multiplier(hrow["class"], hrow["validated"], candle.label, s)

    t = Table(title=f"{sym} agora x histórico (últimos {s.dias_comparacao} dias) | candle de {format_local(int(df_b['open_time'].iloc[-1]), tz)} (Brasília)")
    for col in ("Medida", "Agora", "Percentil", "Leitura", "Amostra"):
        t.add_column(col)
    for c in (candle, day):
        t.add_row(c.name, f"{c.value_pct:.3f}%", f"{c.percentile:.0f}", c.label, f"{c.sample_size} candles/dias")
    console.print(t)
    console.print(
        f"Horário {hour:02d}h: {CLASS_PT[hrow['class']]} ({hrow['pct_above_cost']:.0f}% dos candles passam do custo com folga; "
        f"padrão {'validado' if hrow['validated'] else 'NÃO validado'})."
    )
    console.print(f"Multiplicador de confiança desta análise: [bold]× {ctx.factor:.2f}[/]")
    for r in ctx.reasons:
        console.print(f"  • {r}")
    console.print("[dim]Só reduz a confiança de um sinal; nunca cria um sinal sozinho.[/]")
    return 0

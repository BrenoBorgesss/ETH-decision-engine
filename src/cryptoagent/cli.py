"""Comandos de terminal.

  cryptoagent dados status              foto dos dados agora (preços, candles, derivativos, saúde)
  cryptoagent dados ao-vivo             acompanha preço e candles pelo WebSocket
  cryptoagent historico baixar          baixa/atualiza o histórico (Parquet)
  cryptoagent historico verificar       mostra cobertura e buracos do histórico
  cryptoagent custos coletar            coleta spread/profundidade/slippage (deixe rodando dias)
  cryptoagent custos resumo             quantos dias de coleta existem e spread médio
  cryptoagent sessao iniciar            responde as perguntas do dia (capital e perda máxima)
  cryptoagent sessao ver                mostra a sessão de hoje
  cryptoagent conta permissoes          confere se a chave é SOMENTE leitura
  cryptoagent conta status              saldo, operações do dia, resultado e limites
  cryptoagent diario adicionar|listar|remover   diário manual (contingência sem chave)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sqlite3
import sys
from pathlib import Path

import httpx
from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.table import Table

from rich.prompt import Confirm, FloatPrompt, Prompt

from cryptoagent import DISCLAIMER, __version__
from cryptoagent.account import journal
from cryptoagent.account.binance_account import split_symbol
from cryptoagent.account.permissions import LABELS, ExcessivePermissions, check_permissions
from cryptoagent.account.status import AccountMonitor, AccountStatus
from cryptoagent.config import Config, Secrets, load_config, load_secrets
from cryptoagent.session import daily
from cryptoagent.storage import agent_db
from cryptoagent.costs.recorder import CostRecorder
from cryptoagent.costs.summary import collection_summary
from cryptoagent.data import history
from cryptoagent.data.health import Check, HealthReport, check_age, check_candle, check_clock, check_divergence
from cryptoagent.data.market_store import CandleBuffer
from cryptoagent.data.rest import BinanceError, BinanceReadOnlyClient
from cryptoagent.data.websocket import BinanceStream
from cryptoagent.logging_setup import LOG_FILE, setup_logging
from cryptoagent.security.redact import redact
from cryptoagent.storage import db
from cryptoagent.timeutils import INTERVAL_MS, format_local, local_trading_day_series, now_ms, trading_day_bounds

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


# ------------------------------------------------------------- dados status


async def cmd_status(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    tz = cfg.fuso.local
    sd = cfg.saude_dados
    report = HealthReport()
    async with BinanceReadOnlyClient(cfg.binance, secrets) as client:
        offset, rtt = await client.sync_time()
        report.add(check_clock(offset, sd.desvio_relogio_max_ms))

        prices = Table(title="Preços agora (melhor compra / melhor venda)")
        for col in ("Par", "Bid", "Ask", "Spread %", "Latência ms", "Recebido (Brasília)"):
            prices.add_column(col)
        for sym in cfg.mercado.simbolos_monitorados:
            try:
                r = await client.get("/api/v3/ticker/bookTicker", {"symbol": sym})
            except BinanceError as exc:
                report.add(Check(f"preço {sym}", False, redact(exc)))
                continue
            bid, ask = float(r.data["bidPrice"]), float(r.data["askPrice"])  # type: ignore[index]
            spread = (ask - bid) / ((ask + bid) / 2) * 100
            prices.add_row(sym, f"{bid:,.2f}", f"{ask:,.2f}", f"{spread:.4f}", f"{r.latency_ms:.0f}", format_local(r.received_ms, tz))
            report.add(check_age(f"preço {sym}", r.received_ms, now_ms(), sd.preco_max_s, r.source))
        console.print(prices)

        sym = cfg.mercado.simbolo_analise
        candles = Table(title=f"Último candle fechado de {sym}")
        for col in ("Intervalo", "Abertura (Brasília)", "Fechamento", "Volume", "% compra agressiva"):
            candles.add_column(col)
        for tf in cfg.mercado.timeframes:
            try:
                df, r = await client.klines(sym, tf, limit=3)
            except BinanceError as exc:
                report.add(Check(f"candles {tf}", False, redact(exc)))
                continue
            # Horários de candle são da Binance: compara com o relógio da Binance, não o do PC.
            server_now = client.server_now_ms()
            closed = df[df["close_time"] < server_now]
            last = closed.iloc[-1] if not closed.empty else None
            report.add(
                check_candle(f"candles {tf}", int(last["close_time"]) if last is not None else None, tf, server_now, sd.candle_tolerancia_s, r.source)
            )
            if last is not None:
                taker = last["taker_buy_base"] / last["volume"] * 100 if last["volume"] else 0
                candles.add_row(tf, format_local(int(last["open_time"]), tz), f"{last['close']:,.2f}", f"{last['volume']:,.2f}", f"{taker:.0f}%")
        console.print(candles)

        # Derivativos: só contexto. Se faltarem, é aviso (não bloqueia).
        try:
            pi = await client.get("/fapi/v1/premiumIndex", {"symbol": sym})
            oi = await client.get("/fapi/v1/openInterest", {"symbol": sym})
            funding = float(pi.data["lastFundingRate"]) * 100  # type: ignore[index]
            console.print(
                f"Derivativos {sym} (futuros, só contexto): funding {funding:+.4f}% | "
                f"próximo funding {format_local(int(pi.data['nextFundingTime']), tz)} | "  # type: ignore[index]
                f"open interest {float(oi.data['openInterest']):,.0f} ETH"  # type: ignore[index]
            )
            report.add(check_age("derivativos", pi.received_ms, now_ms(), sd.derivativos_max_s, pi.source, critical=False))
        except BinanceError as exc:
            report.add(Check("derivativos", False, redact(exc), critical=False))

    start, end = trading_day_bounds(now_ms(), tz, cfg.fuso.inicio_dia_local)
    console.print(
        f"Dia de operação (horário de Brasília): {format_local(start, tz, '%d/%m/%Y %H:%M')} até "
        f"{format_local(end, tz, '%d/%m/%Y %H:%M')} | relógio do PC vs Binance: {offset:+d} ms (ida e volta {rtt:.0f} ms) | "
        f"peso da API usado: {client.used_weight_1m}/{cfg.binance.limite_peso_por_minuto}"
    )
    _print_health(report)
    return 0 if report.ok else 2


# ------------------------------------------------------------ dados ao-vivo


async def cmd_live(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    tz = cfg.fuso.local
    sd = cfg.saude_dados
    sym = cfg.mercado.simbolo_analise
    buffers = {tf: CandleBuffer(sym, tf) for tf in cfg.mercado.timeframes}
    tickers: dict[str, tuple[float, float, int]] = {}
    rest_check: list[Check] = []

    async with BinanceReadOnlyClient(cfg.binance, secrets) as client:
        offset, _ = await client.sync_time()
        for tf, buf in buffers.items():
            df, r = await client.klines(sym, tf, limit=200)
            buf.load_frame(df, client.server_now_ms())

        last_seen: dict[str, int] = {}

        def on_message(stream: str, data: dict, received: int) -> None:
            name = stream.split("@")[0].upper()
            if "@kline_" in stream:
                buffers[data["k"]["i"]].apply_ws_kline(data["k"], received)
            elif stream.endswith("@bookTicker"):
                tickers[name] = (float(data["b"]), float(data["a"]), received)
                last_seen[name] = received
            elif "@depth" in stream:
                # bookTicker só chega quando o preço muda; o depth chega a cada 1 s e
                # prova que o dado continua fresco mesmo com o mercado parado.
                if name not in tickers and data["bids"] and data["asks"]:
                    tickers[name] = (float(data["bids"][0][0]), float(data["asks"][0][0]), received)
                last_seen[name] = received

        monitored = cfg.mercado.simbolos_monitorados
        streams = (
            [f"{sym.lower()}@kline_{tf}" for tf in buffers]
            + [f"{s.lower()}@bookTicker" for s in monitored]
            + [f"{s.lower()}@depth5@1000ms" for s in monitored]
        )
        stream = BinanceStream(cfg.binance.ws_url, streams, on_message, name="ao-vivo")
        stop = asyncio.Event()

        async def divergence_loop() -> None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=5)  # espera o primeiro preço do WebSocket
            except TimeoutError:
                pass
            while not stop.is_set():
                try:
                    r = await client.get("/api/v3/ticker/price", {"symbol": sym})
                    ws_mid = (tickers[sym][0] + tickers[sym][1]) / 2 if sym in tickers else None
                    rest_check[:] = [check_divergence("WebSocket x REST", ws_mid, float(r.data["price"]), sd.divergencia_ws_rest_max_pct)]  # type: ignore[index]
                except BinanceError as exc:
                    rest_check[:] = [Check("WebSocket x REST", False, redact(exc))]
                try:
                    await asyncio.wait_for(stop.wait(), timeout=30)
                except TimeoutError:
                    pass

        def render() -> Table:
            now = now_ms()
            report = HealthReport([check_clock(offset, sd.desvio_relogio_max_ms), *rest_check])
            t = Table(title=f"{sym} ao vivo | {format_local(now, tz)} (Brasília) | WS {'conectado' if stream.connected else 'DESCONECTADO'} | reconexões {stream.reconnects}")
            for col in ("Item", "Valor", "Idade / status"):
                t.add_column(col)
            for s in monitored:
                c = check_age(f"preço {s}", last_seen.get(s), now, sd.preco_max_s, "binance-ws:bookTicker+depth")
                report.add(c)
                bid, ask, _ = tickers.get(s, (None, None, None))
                value = f"{bid:,.4f} / {ask:,.4f}" if bid is not None else "-"
                t.add_row(f"{s} bid/ask", value, ("[green]" if c.ok else "[red]") + c.detail + "[/]")
            for tf, buf in buffers.items():
                last = buf.latest_closed()
                c = check_candle(f"candle {tf}", int(last["close_time"]) if last else None, tf, now + offset, sd.candle_tolerancia_s)
                report.add(c)
                cur = buf.latest()
                t.add_row(f"candle {tf}", f"fech. {last['close']:,.2f} | atual {cur['close']:,.2f}" if last and cur else "-", ("[green]" if c.ok else "[red]") + c.detail + "[/]")
            for c in rest_check:
                t.add_row(c.name, "", ("[green]" if c.ok else "[red]") + c.detail + "[/]")
            t.caption = "Dados OK" if report.ok else "PROBLEMA NOS DADOS -> qualquer sinal seria ESPERAR: " + "; ".join(report.problems)
            return t

        tasks = [asyncio.create_task(stream.run(stop)), asyncio.create_task(divergence_loop())]
        try:
            with Live(render(), console=console, refresh_per_second=2) as live:
                loop = asyncio.get_running_loop()
                end = loop.time() + args.segundos if args.segundos else None
                while end is None or loop.time() < end:
                    await asyncio.sleep(1)
                    live.update(render())
        finally:
            stop.set()
            await asyncio.gather(*tasks, return_exceptions=True)
    return 0


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


# ------------------------------------------------------------------ custos


async def cmd_costs_collect(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
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
    for col in ("Par", "Amostras", "De", "Até (Brasília)", "Dias", "Fins de semana", "Cobertura", "Via REST", "Spread médio", "Mediano", "P95"):
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
        console.print(f"[yellow]Só {days} dia(s) de coleta (mínimo {need}). Ainda NÃO dá para tirar conclusões sobre custos.[/]")
    return 0


# -------------------------------------------------------------- indicadores

REGIME_PT = {
    "tendencia_alta": "tendência de alta",
    "tendencia_baixa": "tendência de baixa",
    "lateral": "lateral",
    "alta_volatilidade": "alta volatilidade",
    "transicao": "transição",
    "indefinido": "indefinido (aquecendo)",
}


async def cmd_indicators_show(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    from cryptoagent.indicators.compute import compute_indicators
    from cryptoagent.indicators.structure import nearest_levels, previous_day_levels, support_resistance

    sym = (args.simbolo or cfg.mercado.simbolo_analise).upper()
    p, tz = cfg.indicadores, cfg.fuso.local
    async with BinanceReadOnlyClient(cfg.binance, secrets) as client:
        await client.sync_time()
        df, _ = await client.klines(sym, args.tf, limit=1000)
        server_now = client.server_now_ms()
    df = df[df["close_time"] < server_now].reset_index(drop=True)  # só candles fechados
    if len(df) < p.warmup_bars:
        console.print(f"[yellow]Só {len(df)} candles; os indicadores precisam de {p.warmup_bars} para estabilizar.[/]")
    ind = compute_indicators(df, p, cfg.regime, tz_name=tz, day_start=cfg.fuso.inicio_dia_local)
    last = ind.iloc[-1]
    pos = len(ind) - 1

    t = Table(title=f"{sym} {args.tf} | candle fechado de {format_local(int(last['open_time']), tz)} (Brasília)")
    t.add_column("Indicador")
    t.add_column("Valor")
    t.add_column("Leitura simples")
    c = last["close"]
    t.add_row("Fechamento", f"{c:,.2f}", "")
    for n in p.ema:
        e = last[f"ema_{n}"]
        t.add_row(f"EMA {n}", f"{e:,.2f}", f"preço {'acima' if c > e else 'abaixo'} | inclinação {last[f'ema_{n}_slope']:+.2f}% em {p.inclinacao_ema_barras} candles")
    t.add_row("VWAP do dia", f"{last['vwap']:,.2f}", f"preço a {last['dist_vwap_atr']:+.2f} ATR do VWAP ({'esticado para baixo' if last['dist_vwap_atr'] <= -2 else 'esticado para cima' if last['dist_vwap_atr'] >= 2 else 'perto da média'})")
    t.add_row("Bollinger %B", f"{last['bb_pctb']:.2f}", "≤ 0 = na/abaixo da banda inferior; ≥ 1 = na/acima da superior")
    t.add_row("RSI", f"{last['rsi']:.1f}", "< 30 sobrevendido; > 70 sobrecomprado")
    t.add_row("Stoch RSI %K / %D", f"{last['stochrsi_k']:.1f} / {last['stochrsi_d']:.1f}", "< 20 baixo; > 80 alto")
    t.add_row("MACD / sinal / hist.", f"{last['macd']:.2f} / {last['macd_signal']:.2f} / {last['macd_hist']:+.2f}", "histograma > 0 = momentum de alta")
    t.add_row("ATR", f"{last['atr']:,.2f} ({last['atr_pct']:.2f}%)", f"volatilidade no percentil {last['atr_pctl']:.0f} das últimas {p.janela_percentil} barras")
    t.add_row("ADX (+DI / −DI)", f"{last['adx']:.1f} ({last['plus_di']:.1f} / {last['minus_di']:.1f})", "> 25 tendência forte; < 20 sem tendência")
    t.add_row("Volume relativo", f"{last['rvol']:.2f}×", f"volume ÷ média dos {p.volume_relativo} candles anteriores")
    t.add_row("Venda agressiva", f"{last['taker_sell_ratio'] * 100:.0f}% do volume", f"volume vendido = {last['sell_vol_vs_peak'] * 100:.0f}% do pico recente")
    t.add_row("Estrutura", str(last["structure"]), "topos/fundos confirmados")
    recent = ind.iloc[-p.pivo_barras * 4 :]
    t.add_row("Divergência RSI recente", "altista" if recent["bull_div"].any() else "baixista" if recent["bear_div"].any() else "nenhuma", "")
    t.add_row("Pavio de rejeição", "altista" if last["bull_rejection"] else "baixista" if last["bear_rejection"] else "não", "")
    t.add_row("[bold]Regime[/]", f"[bold]{REGIME_PT[last['regime']]}[/]", "")
    console.print(t)

    levels = support_resistance(
        ind, ind, at_pos=pos, atr_value=float(last["atr"]), tolerance_atr=p.sr_tolerancia_atr, lookback=p.sr_janela_barras, max_levels=p.sr_max_niveis
    ) + previous_day_levels(ind, pos, tz, cfg.fuso.inicio_dia_local, days=ind["trading_day"])
    sup, res = nearest_levels(levels, c)
    lv = Table(title="Suportes e resistências (conhecidos até agora)")
    for col in ("Nível", "Preço", "Distância", "Toques / origem"):
        lv.add_column(col)
    for level in sorted(levels, key=lambda x: x.price, reverse=True):
        mark = " ← resistência mais próxima" if level is res else " ← suporte mais próximo" if level is sup else ""
        lv.add_row(("resistência" if level.price > c else "suporte") + mark, f"{level.price:,.2f}", f"{(level.price / c - 1) * 100:+.2f}%", f"{level.touches} | {level.source}")
    console.print(lv)
    console.print("[dim]Indicadores são insumos; a decisão COMPRAR/VENDER/ESPERAR vem de: cryptoagent analisar.[/]")
    return 0


async def cmd_indicators_validate(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    import time as _t

    from cryptoagent.indicators.compute import compute_indicators

    store = history.HistoryStore(cfg.path(cfg.historico.pasta))
    sym = (args.simbolo or cfg.mercado.simbolo_analise).upper()
    p = cfg.indicadores
    t = Table(title=f"Validação dos indicadores no histórico real de {sym}")
    for col in ("TF", "Candles", "Tempo", "NaN após aquecimento", "RSI mín–máx", "Dentro das bandas", "VWAP reinicia certo", "Regimes (%)"):
        t.add_column(col)
    for tf in cfg.historico.intervalos.get(sym, []):
        df = store.load(sym, tf)
        if len(df) <= p.warmup_bars:
            continue
        t0 = _t.perf_counter()
        ind = compute_indicators(df, p, cfg.regime, tz_name=cfg.fuso.local, day_start=cfg.fuso.inicio_dia_local)
        elapsed = _t.perf_counter() - t0
        tail = ind.iloc[p.warmup_bars :]
        cols = ["ema_200", "rsi", "macd", "stochrsi_k", "atr", "bb_pctb", "adx", "vwap", "rvol", "atr_pctl"]
        nans = int(tail[cols].isna().sum().sum())
        inside = ((tail["close"] >= tail["bb_lower"]) & (tail["close"] <= tail["bb_upper"])).mean() * 100
        # no 1º candle de cada dia (Brasília) o VWAP tem de ser igual ao preço típico daquele candle
        first = ind[ind["trading_day"] != ind["trading_day"].shift(1)].iloc[1:]
        typical = (first["high"] + first["low"] + first["close"]) / 3
        vwap_ok = f"{int(((first['vwap'] - typical).abs() < 1e-6 * typical).sum())}/{len(first)} dias"
        regimes = tail["regime"].value_counts(normalize=True) * 100
        short = {"tendencia_alta": "tend. alta", "tendencia_baixa": "tend. baixa", "lateral": "lateral", "alta_volatilidade": "alta vol.", "transicao": "transição"}
        reg_txt = ", ".join(f"{short.get(k, k)} {v:.0f}" for k, v in regimes.items())
        t.add_row(tf, f"{len(df):,}", f"{elapsed:.1f} s", str(nans), f"{tail['rsi'].min():.1f}–{tail['rsi'].max():.1f}", f"{inside:.1f}%", vwap_ok, reg_txt)
    console.print(t)
    console.print(
        "[dim]Esperado: 0 NaN depois do aquecimento; RSI entre 0 e 100; ~88–95% dos fechamentos dentro das bandas de 2 desvios "
        "(cripto tem caudas gordas); VWAP reiniciando em todos os dias.[/]"
    )
    return 0


# ------------------------------------------------------- análise por horário

CLASS_PT = {"compensa": "[green]compensa[/]", "neutro": "[yellow]neutro[/]", "nao_compensa": "[red]não compensa[/]"}


def _season_paths(cfg: Config) -> tuple[Path, Path]:
    folder = cfg.path(cfg.sazonalidade.relatorio_pasta)
    return folder / "sazonalidade.json", folder / "sazonalidade.html"


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


# -------------------------------------------------------- motor de decisão

ACTION_STYLE = {"COMPRAR": "bold white on green", "VENDER": "bold white on red", "ESPERAR": "bold black on yellow"}


def print_decision(res, cfg: Config) -> None:
    from cryptoagent.fmt import brl, num, pct

    d = res.decision
    ratio = res.ratio
    tz = cfg.fuso.local
    tq = split_symbol(d.trade_symbol)[1]
    tp = lambda x: num(x * ratio, 2) + f" {tq}"
    console.print()
    from cryptoagent.engine.decision import REGIME_PT as ENGINE_REGIME_PT

    console.print(
        f"[{ACTION_STYLE[d.action]}]  {d.action}  [/]  confiança [bold]{num(d.confidence, 0)}%[/]  |  {d.trade_symbol} {tp(d.price)}  |  "
        f"regime {ENGINE_REGIME_PT.get(d.regime, d.regime)} ({cfg.estrategia.tf_setup})  |  {format_local(d.time_ms, tz)} (Brasília)"
    )
    if d.symbol != d.trade_symbol:
        console.print(f"[dim]Sinais calculados no {d.symbol} (mais líquido); preços traduzidos para o {d.trade_symbol} pela cotação de agora.[/]")
    if d.position_note:
        console.print(f"[cyan]{escape(d.position_note)}[/]")
    if d.blocks:
        console.print("[bold]Por que ESPERAR:[/]" if d.action == "ESPERAR" else "[bold]Travas:[/]")
        for b in d.blocks:
            console.print(f"  • {escape(b)}")
        if d.parts and d.action == "ESPERAR":
            console.print("[dim]Abaixo, a leitura técnica do momento, só para referência: ela não vale como sinal enquanto houver trava.[/]")
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
        t = Table(title=f"Níveis sugeridos em {d.trade_symbol}" + ("" if d.action == "COMPRAR" else " (só referência: a decisão não é COMPRAR)"))
        for col in ("Nível", "Preço", "Distância"):
            t.add_column(col)
        t.add_row("Entrada", tp(lv.entry), "")
        t.add_row("[red]Stop-loss (obrigatório)[/]", tp(lv.stop), pct((lv.stop / lv.entry - 1) * 100, 2, sign=True))
        t.add_row("Alvo 1", tp(lv.target1), pct(lv.target_distance_pct, 2, sign=True) + f" ({lv.target1_source})")
        t.add_row("Alvo 2", tp(lv.target2), pct((lv.target2 / lv.entry - 1) * 100, 2, sign=True))
        t.add_row("Risco/retorno (já com taxas)", num(lv.rr, 2), f"ganho {pct(lv.reward_net_pct)} x risco {pct(lv.risk_net_pct)}")
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
        console.print("[bold yellow]Lembrete: coloque a ordem de STOP (ou OCO) na Binance logo após comprar. O agente não envia ordens.[/]")
    if res.hour_note:
        console.print(f"[dim]Horário: {escape(res.hour_note)}[/]")
    ages = ", ".join(f"{k} {num(v, 1)}" for k, v in res.data_ages.items() if v >= 0)
    console.print(f"[dim]Idade dos dados (s): {ages} | latência da análise {num(res.latency_ms, 0)} ms | {d.disclaimer}[/]")


async def cmd_analyze(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    from cryptoagent.engine.journal import save_signal
    from cryptoagent.engine.live import analyze_live

    conn = agent_db.connect(cfg.path(cfg.conta.banco))
    try:
        async with BinanceReadOnlyClient(cfg.binance, secrets) as client:
            res = await analyze_live(cfg, secrets, client, conn)
        signal_id = save_signal(
            conn, res.decision, ratio=res.ratio, mode="ao_vivo", data_ages=res.data_ages, latency_ms=res.latency_ms, market_extras=res.market_extras
        )
    finally:
        conn.close()
    print_decision(res, cfg)
    console.print(f"[dim]Registrado como sinal #{signal_id}.[/]")
    return 0


async def cmd_signals_list(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    from cryptoagent.fmt import num

    conn = agent_db.connect(cfg.path(cfg.conta.banco))
    try:
        rows = conn.execute("SELECT * FROM signals WHERE mode = 'ao_vivo' ORDER BY time_ms DESC LIMIT ?", (args.n,)).fetchall()
        trades = conn.execute(
            "SELECT symbol, trade_id, time_ms, price, qty, signal_id, association FROM account_trades WHERE is_buyer = 1 ORDER BY time_ms DESC LIMIT 20"
        ).fetchall()
    finally:
        conn.close()
    t = Table(title="Últimos sinais")
    for col in ("#", "Quando (Brasília)", "Par", "Decisão", "Confiança", "Preço", "Stop", "Alvo 1"):
        t.add_column(col)
    for r in rows:
        t.add_row(str(r["id"]), format_local(r["time_ms"], cfg.fuso.local), r["trade_symbol"], r["action"], f"{num(r['confidence'], 0)}%", num(r["price"]), num(r["stop"]), num(r["target1"]))
    console.print(t)
    if trades:
        a = Table(title="Suas compras e o sinal associado (confirme ou corrija com: sinais associar)")
        for col in ("Par", "Operação", "Quando", "Preço", "Qtd", "Sinal", "Situação"):
            a.add_column(col)
        for r in trades:
            a.add_row(r["symbol"], str(r["trade_id"]), format_local(r["time_ms"], cfg.fuso.local), num(r["price"]), f"{r['qty']:g}", f"#{r['signal_id']}" if r["signal_id"] else "-", r["association"])
        console.print(a)
    return 0


async def cmd_signals_associate(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    from cryptoagent.engine.journal import set_association

    conn = agent_db.connect(cfg.path(cfg.conta.banco))
    try:
        signal_id = None if args.sinal in ("nenhum", "0") else int(args.sinal)
        set_association(conn, args.par.upper(), args.operacao, signal_id)
    finally:
        conn.close()
    console.print(f"Compra {args.operacao} associada a {'nenhum sinal' if signal_id is None else f'sinal #{signal_id}'}.")
    return 0


# ----------------------------------------------------------------- backtest


async def cmd_backtest(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    from cryptoagent.backtest.costs import build_schedule
    from cryptoagent.backtest.data import load_backtest_data
    from cryptoagent.backtest.report import COLS, _fmt, render_html, save_json
    from cryptoagent.backtest.runner import run_pair

    bt = cfg.backtest
    pairs = [x.upper() for x in args.pares] if args.pares else bt.pares
    fees = await _account_taker_fees(cfg, secrets, pairs)
    reports = []
    for pair in pairs:
        fee, fee_source = fees.get(pair, (cfg.custos.taxa_padrao_taker, "do config (sem leitura da conta)"))
        costs = build_schedule(
            pair, fee, cfg.sazonalidade.spread_padrao_pct.get(pair, 0.05), cfg.path(cfg.custos.banco), cfg.fuso.local,
            min_samples=bt.amostras_min_spread_hora, slip_market=bt.slippage_mercado_pct, slip_stop=bt.slippage_stop_pct, fee_source=fee_source,
        )
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

    folder = cfg.path(bt.relatorio_pasta)
    save_json(reports, folder / "backtest.json")
    render_html(reports, folder / "backtest.html", capital=bt.capital, tz=cfg.fuso.local, train_months=bt.meses_treino, default_distance=cfg.estrategia.distancia_alvo_min_custo)
    console.print(f"\nRelatório completo (com ablação, walk-forward mês a mês e motivos): [bold]{folder / 'backtest.html'}[/]")
    console.print("[dim]Resultados passados não garantem resultados futuros.[/]")
    return 0


# ------------------------------------------------------------------- painel


async def cmd_panel(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    import uvicorn
    import webbrowser

    from cryptoagent.dashboard.server import create_app
    from cryptoagent.dashboard.service import LiveService

    service = LiveService(cfg, secrets, console)
    app = create_app(service)
    port = args.porta or cfg.painel.porta
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False))
    url = f"http://127.0.0.1:{port}/"
    console.print(
        f"Painel em [bold]{url}[/] (só neste computador). Análise a cada fechamento de {', '.join(cfg.painel.recalcular_ao_fechar)}"
        + (f" e a cada {cfg.painel.analise_extra_a_cada_s:g} s" if cfg.painel.analise_extra_a_cada_s else "")
        + ". Ctrl+C para parar."
    )
    if cfg.painel.abrir_navegador and not args.sem_navegador:
        asyncio.get_running_loop().call_later(3, webbrowser.open, url)
    await service.run(extra_tasks=[asyncio.create_task(server.serve())])
    server.should_exit = True
    if service.fatal:
        console.print(f"[bold red]RECUSADO:[/] {escape(service.fatal)}")
        return 3
    return 0


async def cmd_alert_test(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    from cryptoagent.alerts.notify import Alert, Alerter

    Alerter(cfg.alertas, console, cfg.fuso.local).send(Alert(now_ms(), "Teste de alerta do agente", "Se você está vendo isto, as notificações funcionam."))
    console.print("Enviado. Se a notificação do Windows não apareceu, confira em Configurações → Sistema → Notificações se o Windows PowerShell pode notificar.")
    return 0


# ----------------------------------------------------------- paper trading


async def cmd_paper_run(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    from cryptoagent.paper.runner import run_paper

    fees = await _account_taker_fees(cfg, secrets, [cfg.paper.par])
    fee, source = fees.get(cfg.paper.par, (cfg.custos.taxa_padrao_taker, "do config"))
    console.print(f"[dim]Taxa usada na carteira virtual: {fee * 100:.4f}% por lado ({source}).[/]")
    await run_paper(cfg, secrets, fee=fee, max_cycles=args.ciclos, say=lambda msg: console.print(escape(msg)))
    return 0


def _print_gate_study(rep, min_trades: int) -> None:
    from cryptoagent.fmt import pct
    from cryptoagent.paper.report import GATE_PT

    console.print(
        "[bold]Variante de ESTUDO:[/] compra virtual mesmo quando só travas de previsão seguraram "
        "(tendência, regime, esticado, gatilho, confiança). As de segurança e as de custo continuam valendo."
    )
    if not rep.gates:
        console.print("[dim]Nenhuma operação de estudo fechada ainda.[/]")
        return
    t = Table(title="Cada trava: operações que ela teria BARRADO x as que deixaria passar (% líquido, com taxas)")
    for col in ("Trava", "Barradas: n", "acerto", "média", "Liberadas: n", "acerto", "média"):
        t.add_column(col)
    f = lambda v, s=False: "-" if v is None else pct(v, 3 if s else 0, sign=s)
    for g in rep.gates:
        t.add_row(GATE_PT[g.gate], str(g.n_blocked), f(g.win_blocked_pct), f(g.avg_blocked_pct, True), str(g.n_passed), f(g.win_passed_pct), f(g.avg_passed_pct, True))
    console.print(t)
    console.print(
        "[dim]Se as 'barradas' dão média pior que as 'liberadas', a trava está ajudando. Uma trava só deve ser revista com "
        f"pelo menos {min_trades} operações em CADA lado, e como hipótese declarada testada no backtest — nunca mudando direto.[/]"
    )


async def cmd_paper_report(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    from cryptoagent.fmt import num, pct
    from cryptoagent.paper.report import OUTCOMES, load_backtest_json, variant_report
    from cryptoagent.paper.runner import load_variants

    pc = cfg.paper
    store = history.HistoryStore(cfg.path(cfg.historico.pasta))
    async with BinanceReadOnlyClient(cfg.binance, secrets) as client:
        await client.sync_time()
        await history.update_from_rest(store, client, pc.par, "1m", months=cfg.historico.meses, now=client.server_now_ms())
    bars = store.load(pc.par, "1m")
    conn = agent_db.connect(cfg.path(cfg.conta.banco))
    try:
        for name, vcfg in load_variants(cfg, pc.variantes):
            bt_json = load_backtest_json(vcfg.path(vcfg.backtest.relatorio_pasta) / "backtest.json")
            rep = variant_report(conn, name, pc.capital, bars, pc.horizonte_avaliacao_h, pc.operacoes_min_conclusao, bt_json, pc.par)
            m = rep.metrics
            console.rule(f"Paper trading: {name} ({pc.par}) desde {format_local(rep.started_ms, cfg.fuso.local, '%d/%m/%Y %H:%M')}")
            console.print(
                f"Patrimônio virtual: [bold]{num(rep.equity)}[/] (início {num(pc.capital)}) | operações fechadas: {m['operacoes']} | "
                f"acerto {pct(m['taxa_acerto_pct'], 1)} | média por operação {pct(m['expectativa_pct'], 3, sign=True)} | drawdown {pct(m['drawdown_max_pct'], 2)}"
            )
            if rep.backtest_expectancy_pct is not None:
                weak = " [yellow](amostra pequena demais para valer como referência)[/]" if rep.backtest_trades < pc.operacoes_min_conclusao else ""
                console.print(
                    f"Backtest fora da amostra: {pct(rep.backtest_expectancy_pct, 3, sign=True)} por operação, em {rep.backtest_trades} operação(ões){weak}."
                )
            if rep.open_trade:
                o = rep.open_trade
                console.print(f"Posição virtual aberta: entrada {num(o['entry_price'])} | stop {num(o['stop'])} | alvo {num(o['target1'])} | desde {format_local(o['entry_ms'], cfg.fuso.local)}")
            console.print("Sinais registrados: " + (", ".join(f"{k} {v}" for k, v in rep.signals_by_action.items()) or "nenhum ainda"))
            if rep.outcomes:
                t = Table(title=f"O que o mercado fez nas {pc.horizonte_avaliacao_h:g} h seguintes (usando entrada, stop e alvo de cada sinal)")
                for col in ("Sinal", "Alvo c/ lucro", "Alvo s/ lucro", "Stop primeiro", "Nenhum", "Aguardando"):
                    t.add_column(col)
                for action, res in rep.outcomes.items():
                    t.add_row(action, *(str(res.get(k, 0)) for k in OUTCOMES))
                console.print(t)
                console.print(
                    "[dim]'ESPERAR (com níveis)' = a leitura tinha entrada/stop/alvo, mas alguma trava segurou. "
                    "'Alvo s/ lucro' = o preço chegou ao alvo, mas o alvo não pagava taxas + spread: na prática, prejuízo. "
                    "Só 'Alvo c/ lucro' conta como acerto.[/]"
                )
            if rep.calibration:
                c = Table(title="Calibração: a confiança acompanha a taxa de acerto?")
                for col in ("Faixa de confiança", "Sinais resolvidos", "% alvo com lucro"):
                    c.add_column(col)
                for band, n, rate in rep.calibration:
                    c.add_row(band, str(n), pct(rate, 0))
                console.print(c)
            if vcfg.paper.estudo:
                _print_gate_study(rep, pc.operacoes_min_conclusao)
            if not rep.enough:
                console.print(f"[yellow]Só {m['operacoes']} operação(ões) virtual(is) fechada(s) (mínimo {pc.operacoes_min_conclusao}): ainda NÃO dá para tirar conclusões.[/]")
    finally:
        conn.close()
    return 0


# ------------------------------------------------------------ sessão do dia


def _session_table(s: daily.DailySession, cfg: Config) -> Table:
    t = Table(title=f"Sessão do dia {s.day.strftime('%d/%m/%Y')} (horário de Brasília)")
    t.add_column("Campo")
    t.add_column("Valor")
    t.add_row("Capital de hoje", f"R$ {s.capital_brl:,.2f}")
    t.add_row("Perda máxima de hoje", f"R$ {s.max_loss_brl:,.2f} ({s.max_loss_brl / s.capital_brl * 100:.1f}% do capital)")
    t.add_row("Risco por operação", f"R$ {s.max_loss_brl / cfg.risco.divisor_perda_diaria:,.2f} (perda máxima ÷ {cfg.risco.divisor_perda_diaria:g})")
    t.add_row("Par da operação", s.par_operacao)
    t.add_row("Saldo mantido em", s.saldo_em)
    t.add_row("Atualizada em", format_local(s.updated_ms, cfg.fuso.local))
    return t


async def cmd_session_start(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
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

        default_capital = last.capital_brl if last else (cfg.capital_operacao.valor if cfg.capital_operacao.modo == "brl" else None)
        capital = args.capital if args.capital is not None else ask_float("(1) Com quantos R$ você quer começar hoje?", default_capital)
        max_loss = args.perda_max if args.perda_max is not None else ask_float(
            "(2) Qual o MÁXIMO que você aceita perder hoje, em R$?", existing.max_loss_brl if existing else None
        )
        par = (args.par or (existing.par_operacao if existing else None) or cfg.mercado.par_operacao or "").upper()
        if not par:
            par = Prompt.ask("Em qual par você vai operar hoje?", choices=candidates, default=last.par_operacao if last else candidates[0])
        if par.endswith("BRL"):
            saldo = "BRL"
        else:
            saldo = (args.saldo or "").upper() or Prompt.ask(
                "Você mantém o saldo da operação em USDT ou em BRL?", choices=["USDT", "BRL"], default=last.saldo_em if last else "USDT"
            )
        session = daily.save_session(
            conn, day, capital_brl=capital, max_loss_brl=max_loss, par_operacao=par, saldo_em=saldo, candidates=candidates
        )
    except daily.SessionError as exc:
        console.print(f"[red]Sessão não salva:[/] {escape(str(exc))}")
        return 1
    finally:
        conn.close()
    console.print(_session_table(session, cfg))
    if session.max_loss_brl > session.capital_brl * cfg.risco.aviso_perda_max_pct_capital / 100:
        console.print(f"[yellow]Atenção: a perda máxima passa de {cfg.risco.aviso_perda_max_pct_capital:g}% do capital.[/]")
    return 0


async def cmd_session_show(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    conn = agent_db.connect(cfg.path(cfg.conta.banco))
    try:
        session = daily.get_session(conn, daily.today(cfg.fuso.local, cfg.fuso.inicio_dia_local))
    finally:
        conn.close()
    if session is None:
        console.print("[yellow]Sessão de hoje ainda não respondida: o agente fica em ESPERAR. Rode: cryptoagent sessao iniciar[/]")
        return 1
    console.print(_session_table(session, cfg))
    return 0


# ------------------------------------------------------------------- conta


async def cmd_account_permissions(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
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
            t.add_row("restrição por IP", "sim" if value else "não", "[green]bom[/]" if value else "[yellow]recomendado ligar[/]")
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
    console.print(f"Modo da conta: [bold]{st.mode}[/] ({'verificado pela Binance' if st.verified else 'NÃO verificado'})")
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
        bnb = f"desconto BNB ligado (paga {c.bnb_discount_factor * 100:.0f}% da taxa)" if c.bnb_discount_enabled else "desconto BNB desligado"
        console.print(f"Taxas {origem}: maker {c.maker * 100:.4f}% | taker {c.taker * 100:.4f}% | {bnb} | taxa usada para avaliar a saída: {st.exit_fee_rate * 100:.4f}%")
    if st.day and st.symbol:
        d = st.day
        base, quote = split_symbol(st.symbol)
        to_brl = st.fx_usdtbrl if quote == "USDT" else 1.0
        t = Table(title=f"Operação de hoje em {st.symbol}")
        t.add_column("Item")
        t.add_column("Valor")
        t.add_row("Posição de operação aberta", f"{d.open_qty:.6f} {base}" + (f" (custo médio real {d.avg_cost_real:,.2f} {quote})" if d.avg_cost_real else ""))
        if d.carried_qty > 1e-12:
            t.add_row("Veio de ontem", f"{d.carried_qty:.6f} {base}, reavaliado a {d.day_start_price:,.2f} (00:00)")
        t.add_row("Operações hoje", str(d.trades_today))
        t.add_row("Realizado hoje", f"{d.realized_quote:+,.2f} {quote}")
        t.add_row("Posição aberta (saída agora)", f"{d.unrealized_quote:+,.2f} {quote} (bid {d.mark_price:,.2f}, já com taxa de venda)")
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
        console.print("[bold green]Conta em ordem: limites permitem sinais.[/] (A decisão em si: cryptoagent analisar.)")


async def cmd_account_status(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
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
    conn = agent_db.connect(cfg.path(cfg.conta.banco))
    try:
        symbol = _journal_symbol(cfg, conn, args.par)
        side = {"compra": "buy", "venda": "sell"}[args.lado]
        fee = args.taxa if args.taxa is not None else args.qtd * args.preco * cfg.custos.taxa_padrao_taker
        trade_id = journal.add_manual_trade(conn, symbol=symbol, side=side, qty=args.qtd, price=args.preco, fee_quote=fee, note=args.nota or "")
    finally:
        conn.close()
    _, quote = split_symbol(symbol)
    console.print(f"Registrado #{trade_id}: {args.lado} de {args.qtd} a {args.preco:,.2f} {quote} em {symbol} (taxa {fee:,.4f} {quote}).")
    if cfg.conta.modo != "diario_manual":
        console.print("[yellow]Obs.: o diário só é usado com conta.modo: diario_manual no config.[/]")
    return 0


async def cmd_journal_list(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
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
        t.add_row(str(r["id"]), format_local(r["time_ms"], cfg.fuso.local), "compra" if r["side"] == "buy" else "venda", f"{r['qty']:g}", f"{r['price']:,.2f}", f"{r['fee_quote']:,.4f}", r["note"] or "")
    console.print(t)
    return 0


async def cmd_journal_remove(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    conn = agent_db.connect(cfg.path(cfg.conta.banco))
    try:
        ok = journal.remove_manual_trade(conn, args.id)
    finally:
        conn.close()
    console.print(f"Registro #{args.id} removido." if ok else f"[yellow]Registro #{args.id} não encontrado.[/]")
    return 0 if ok else 1


# -------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cryptoagent", description="Assistente de decisão cripto (somente leitura).")
    parser.add_argument("--config", type=Path, default=None, help="caminho do config.yaml")
    parser.add_argument("--sobrepor", type=Path, default=None, help="YAML com campos que substituem os do config (ex.: uma hipótese)")
    parser.add_argument("--version", action="version", version=f"cryptoagent {__version__}")
    sub = parser.add_subparsers(dest="grupo")

    dados = sub.add_parser("dados", help="dados de mercado").add_subparsers(dest="acao")
    p = dados.add_parser("status", help="foto dos dados agora e saúde")
    p.set_defaults(func=cmd_status)
    p = dados.add_parser("ao-vivo", help="acompanha preço e candles pelo WebSocket")
    p.add_argument("--segundos", type=float, default=None, help="para depois de N segundos")
    p.set_defaults(func=cmd_live)

    hist = sub.add_parser("historico", help="histórico de candles").add_subparsers(dest="acao")
    p = hist.add_parser("baixar", help="baixa/atualiza o histórico")
    p.add_argument("--simbolos", nargs="*", default=None)
    p.add_argument("--meses", type=int, default=None)
    p.set_defaults(func=cmd_history_download)
    p = hist.add_parser("verificar", help="cobertura e buracos")
    p.add_argument("--simbolos", nargs="*", default=None)
    p.set_defaults(func=cmd_history_check)

    custos = sub.add_parser("custos", help="coleta e resumo de custos").add_subparsers(dest="acao")
    p = custos.add_parser("coletar", help="coleta spread/profundidade/slippage (deixe rodando)")
    p.add_argument("--segundos", type=float, default=None, help="para depois de N segundos (teste)")
    p.set_defaults(func=cmd_costs_collect)
    p = custos.add_parser("resumo", help="quantos dias já foram coletados")
    p.set_defaults(func=cmd_costs_summary)

    indic = sub.add_parser("indicadores", help="indicadores técnicos").add_subparsers(dest="acao")
    p = indic.add_parser("ver", help="indicadores do último candle fechado (dados ao vivo)")
    p.add_argument("--tf", default="15m", choices=list(INTERVAL_MS))
    p.add_argument("--simbolo", default=None)
    p.set_defaults(func=cmd_indicators_show)
    p = indic.add_parser("validar", help="confere os indicadores em todo o histórico baixado")
    p.add_argument("--simbolo", default=None)
    p.set_defaults(func=cmd_indicators_validate)

    p = sub.add_parser("analisar", help="decisão agora: COMPRAR / VENDER / ESPERAR, com motivos e níveis")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("painel", help="painel ao vivo no navegador + alertas")
    p.add_argument("--porta", type=int, default=None)
    p.add_argument("--sem-navegador", dest="sem_navegador", action="store_true", help="não abre o navegador sozinho")
    p.set_defaults(func=cmd_panel)

    alertas = sub.add_parser("alertas", help="alertas").add_subparsers(dest="acao")
    p = alertas.add_parser("testar", help="manda um alerta de teste (terminal + Windows)")
    p.set_defaults(func=cmd_alert_test)

    paper = sub.add_parser("paper", help="paper trading: sinais ao vivo com carteira virtual").add_subparsers(dest="acao")
    p = paper.add_parser("rodar", help="roda o paper trading (deixe aberto)")
    p.add_argument("--ciclos", type=int, default=None, help="para depois de N minutos (teste)")
    p.set_defaults(func=cmd_paper_run)
    p = paper.add_parser("relatorio", help="sinais registrados x o que o mercado fez")
    p.set_defaults(func=cmd_paper_report)

    p = sub.add_parser("backtest", help="testa a estratégia no histórico (walk-forward, ablação, comparações)")
    p.add_argument("--pares", nargs="*", default=None, help="pares de operação (padrão: backtest.pares do config)")
    p.add_argument("--rapido", action="store_true", help="só parâmetros padrão + comparações (sem grade nem ablação)")
    p.set_defaults(func=cmd_backtest)

    sinais = sub.add_parser("sinais", help="histórico de sinais e associação com suas compras").add_subparsers(dest="acao")
    p = sinais.add_parser("listar", help="últimos sinais e compras associadas")
    p.add_argument("-n", type=int, default=20)
    p.set_defaults(func=cmd_signals_list)
    p = sinais.add_parser("associar", help="confirma ou corrige o sinal de uma compra")
    p.add_argument("--par", required=True)
    p.add_argument("--operacao", required=True, type=int, help="id da operação (coluna Operação em sinais listar)")
    p.add_argument("--sinal", required=True, help="número do sinal, ou 'nenhum'")
    p.set_defaults(func=cmd_signals_associate)

    saz = sub.add_parser("sazonalidade", help="análise por horário").add_subparsers(dest="acao")
    p = saz.add_parser("analisar", help="perfil por hora/dia + validação walk-forward + mapa de calor")
    p.add_argument("--simbolos", nargs="*", default=None)
    p.set_defaults(func=cmd_season_analyze)
    p = saz.add_parser("agora", help="o movimento de agora x o histórico da mesma hora")
    p.add_argument("--simbolo", default=None)
    p.set_defaults(func=cmd_season_now)

    sessao = sub.add_parser("sessao", help="perguntas do início do dia").add_subparsers(dest="acao")
    p = sessao.add_parser("iniciar", help="responde (ou ajusta) a sessão de hoje")
    p.add_argument("--capital", type=float, default=None, help="capital de hoje em R$")
    p.add_argument("--perda-max", dest="perda_max", type=float, default=None, help="perda máxima de hoje em R$")
    p.add_argument("--par", default=None, help="par em que vai operar (ETHBRL ou ETHUSDT)")
    p.add_argument("--saldo", default=None, choices=["USDT", "BRL", "usdt", "brl"], help="onde mantém o saldo da operação")
    p.set_defaults(func=cmd_session_start)
    p = sessao.add_parser("ver", help="mostra a sessão de hoje")
    p.set_defaults(func=cmd_session_show)

    conta = sub.add_parser("conta", help="conta da Binance (somente leitura)").add_subparsers(dest="acao")
    p = conta.add_parser("permissoes", help="confere se a chave é somente leitura")
    p.set_defaults(func=cmd_account_permissions)
    p = conta.add_parser("status", help="saldo, operações do dia, resultado e limites")
    p.set_defaults(func=cmd_account_status)

    diario = sub.add_parser("diario", help="diário manual (contingência sem chave)").add_subparsers(dest="acao")
    p = diario.add_parser("adicionar", help="registra uma operação feita por você")
    p.add_argument("--lado", required=True, choices=["compra", "venda"])
    p.add_argument("--qtd", required=True, type=float, help="quantidade de ETH")
    p.add_argument("--preco", required=True, type=float, help="preço por ETH na moeda do par")
    p.add_argument("--taxa", type=float, default=None, help="taxa paga, na moeda do par (padrão: estimada pelo config)")
    p.add_argument("--par", default=None)
    p.add_argument("--nota", default=None)
    p.set_defaults(func=cmd_journal_add)
    p = diario.add_parser("listar", help="lista o diário")
    p.add_argument("--par", default=None)
    p.set_defaults(func=cmd_journal_list)
    p = diario.add_parser("remover", help="remove um registro do diário")
    p.add_argument("--id", required=True, type=int)
    p.set_defaults(func=cmd_journal_remove)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    try:
        cfg = load_config(args.config, args.sobrepor)
    except Exception as exc:
        console.print(f"[red]Erro no arquivo de configuração:[/] {escape(str(exc))}")
        return 1
    secrets = load_secrets(cfg.root)
    setup_logging(cfg, secrets)
    try:
        return asyncio.run(args.func(cfg, secrets, args)) or 0
    except KeyboardInterrupt:
        console.print("Encerrado pelo usuário.")
        return 130
    except ExcessivePermissions as exc:
        log.error("Recusado: chave com permissões além de leitura")
        console.print(f"[bold red]RECUSADO:[/] {escape(str(exc))}")
        return 3
    except Exception as exc:
        log.exception("Erro não tratado")
        console.print(f"[red]Erro:[/] {escape(redact(exc))}")
        console.print(f"[dim]Detalhes técnicos em {cfg.path(cfg.logs.pasta) / LOG_FILE}[/]")
        return 1
    finally:
        if args.func is not cmd_analyze:  # a decisão já imprime o aviso junto com o sinal
            console.print(f"[dim]{DISCLAIMER}[/]")

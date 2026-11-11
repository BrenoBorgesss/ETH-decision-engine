"""Comandos `dados status` e `dados ao-vivo`: foto e acompanhamento dos dados de mercado."""

from __future__ import annotations

import argparse
import asyncio
import contextlib

from rich.live import Live
from rich.table import Table

from cryptoagent.cli._shared import _print_health, console
from cryptoagent.config import Config, Secrets
from cryptoagent.data.health import Check, HealthReport, check_age, check_candle, check_clock, check_divergence
from cryptoagent.data.market_store import CandleBuffer
from cryptoagent.data.rest import BinanceError, BinanceReadOnlyClient
from cryptoagent.data.websocket import BinanceStream
from cryptoagent.security.redact import redact
from cryptoagent.timeutils import format_local, now_ms, trading_day_bounds

# ------------------------------------------------------------- dados status


async def cmd_status(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    """Comando `dados status`: mostra preços, últimos candles, derivativos e a saúde dos dados agora. Devolve o código de saída do processo."""
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
            prices.add_row(
                sym,
                f"{bid:,.2f}",
                f"{ask:,.2f}",
                f"{spread:.4f}",
                f"{r.latency_ms:.0f}",
                format_local(r.received_ms, tz),
            )
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
                check_candle(
                    f"candles {tf}",
                    int(last["close_time"]) if last is not None else None,
                    tf,
                    server_now,
                    sd.candle_tolerancia_s,
                    r.source,
                )
            )
            if last is not None:
                taker = last["taker_buy_base"] / last["volume"] * 100 if last["volume"] else 0
                candles.add_row(
                    tf,
                    format_local(int(last["open_time"]), tz),
                    f"{last['close']:,.2f}",
                    f"{last['volume']:,.2f}",
                    f"{taker:.0f}%",
                )
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
            report.add(
                check_age("derivativos", pi.received_ms, now_ms(), sd.derivativos_max_s, pi.source, critical=False)
            )
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
    """Comando `dados ao-vivo`: acompanha preço e candles pelo WebSocket numa tabela que se atualiza. Devolve o código de saída do processo."""
    tz = cfg.fuso.local
    sd = cfg.saude_dados
    sym = cfg.mercado.simbolo_analise
    buffers = {tf: CandleBuffer(sym, tf) for tf in cfg.mercado.timeframes}
    tickers: dict[str, tuple[float, float, int]] = {}
    rest_check: list[Check] = []

    async with BinanceReadOnlyClient(cfg.binance, secrets) as client:
        offset, _ = await client.sync_time()
        for tf, buf in buffers.items():
            df, _r = await client.klines(sym, tf, limit=200)
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
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=5)  # espera o primeiro preço do WebSocket
            while not stop.is_set():
                try:
                    r = await client.get("/api/v3/ticker/price", {"symbol": sym})
                    ws_mid = (tickers[sym][0] + tickers[sym][1]) / 2 if sym in tickers else None
                    rest_check[:] = [
                        check_divergence(
                            "WebSocket x REST", ws_mid, float(r.data["price"]), sd.divergencia_ws_rest_max_pct
                        )
                    ]  # type: ignore[index]
                except BinanceError as exc:
                    rest_check[:] = [Check("WebSocket x REST", False, redact(exc))]
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=30)

        def render() -> Table:
            now = now_ms()
            report = HealthReport([check_clock(offset, sd.desvio_relogio_max_ms), *rest_check])
            t = Table(
                title=f"{sym} ao vivo | {format_local(now, tz)} (Brasília) | WS {'conectado' if stream.connected else 'DESCONECTADO'} | reconexões {stream.reconnects}"
            )
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
                c = check_candle(
                    f"candle {tf}", int(last["close_time"]) if last else None, tf, now + offset, sd.candle_tolerancia_s
                )
                report.add(c)
                cur = buf.latest()
                t.add_row(
                    f"candle {tf}",
                    f"fech. {last['close']:,.2f} | atual {cur['close']:,.2f}" if last and cur else "-",
                    ("[green]" if c.ok else "[red]") + c.detail + "[/]",
                )
            for c in rest_check:
                t.add_row(c.name, "", ("[green]" if c.ok else "[red]") + c.detail + "[/]")
            t.caption = (
                "Dados OK"
                if report.ok
                else "PROBLEMA NOS DADOS -> qualquer sinal seria ESPERAR: " + "; ".join(report.problems)
            )
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

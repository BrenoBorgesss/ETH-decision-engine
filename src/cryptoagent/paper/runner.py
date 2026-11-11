"""Loop do paper trading: a cada minuto confere a OCO virtual e, a cada fechamento do candle de
gatilho de cada variante, roda o MESMO motor ao vivo e registra a decisão.

Dados: consulta periódica à API REST (com checagem de saúde a cada análise). O tempo real por
WebSocket fica no painel (cryptoagent painel).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cryptoagent.config import Config, Secrets, load_config
from cryptoagent.data.rest import BinanceError, BinanceReadOnlyClient
from cryptoagent.engine.decision import study_entry_ok
from cryptoagent.engine.journal import save_signal
from cryptoagent.engine.live import analyze_live
from cryptoagent.fmt import num, pct
from cryptoagent.paper.book import PaperBook, PaperCosts
from cryptoagent.security.redact import redact
from cryptoagent.storage import agent_db
from cryptoagent.timeutils import floor_to_interval, format_local

log = logging.getLogger(__name__)


@dataclass
class Variant:
    name: str
    cfg: Config
    book: PaperBook


def load_variants(cfg: Config, overlay_paths: list[str]) -> list[tuple[str, Config]]:
    out = []
    for item in overlay_paths:
        if item == "padrao":
            out.append(("padrao", cfg))
        else:
            path = cfg.path(item)
            out.append((Path(item).stem, load_config(cfg.root / "config" / "config.yaml", path)))
    return out


async def run_paper(
    cfg: Config, secrets: Secrets, *, fee: float, max_cycles: int | None = None, say: Callable[[str], None] = print, sleep=asyncio.sleep
) -> None:
    pc = cfg.paper
    conn = agent_db.connect(cfg.path(cfg.conta.banco))
    costs = PaperCosts(fee, cfg.backtest.slippage_mercado_pct, cfg.backtest.slippage_stop_pct)
    variants = [
        Variant(name, vcfg, PaperBook(conn, name, pc.par, capital=pc.capital, loss_pct=pc.perda_max_dia_pct, divisor=cfg.risco.divisor_perda_diaria,
                                      costs=costs, tz=cfg.fuso.local, day_start=cfg.fuso.inicio_dia_local))
        for name, vcfg in load_variants(cfg, pc.variantes)
    ]
    say(
        f"Paper trading em {pc.par} com {len(variants)} variante(s): {', '.join(v.name for v in variants)}. "
        f"Capital virtual {num(pc.capital)}; taxa {pct(fee * 100, 3)}/lado. Nenhuma ordem é enviada. Ctrl+C para parar."
    )
    cycles = 0
    try:
        async with BinanceReadOnlyClient(cfg.binance, secrets) as client:
            while True:
                await client.sync_time()
                now = client.server_now_ms()
                try:
                    bt = await client.get("/api/v3/ticker/bookTicker", {"symbol": pc.par})
                    bid, ask = float(bt.data["bidPrice"]), float(bt.data["askPrice"])  # type: ignore[index]
                    half = (ask - bid) / (ask + bid)
                    for v in variants:
                        await _cycle(v, cfg, secrets, client, conn, now, bid, ask, half, say)
                except BinanceError as exc:
                    say(f"[aviso] falha de dados neste ciclo ({redact(exc)}); nada foi decidido. Tenta de novo no próximo minuto.")
                cycles += 1
                if max_cycles is not None and cycles >= max_cycles:
                    break
                # acorda logo depois do próximo fechamento de minuto
                next_min = floor_to_interval(client.server_now_ms(), "1m") + 60_000
                await sleep(max(1.0, (next_min - client.server_now_ms()) / 1000 + pc.atraso_s))
    finally:
        conn.close()


async def _cycle(v: Variant, cfg: Config, secrets: Secrets, client: BinanceReadOnlyClient, conn, now: int, bid: float, ask: float, half: float, say) -> None:
    pc, book, tz = cfg.paper, v.book, cfg.fuso.local
    # 1) OCO virtual nos candles de 1m fechados desde a última verificação
    pos = book.open_trade()
    if pos is not None:
        bars, _ = await client.klines(pc.par, "1m", start_ms=int(pos["checked_until_ms"]) - 60_000, limit=1000)
        bars = bars[bars["close_time"] < now]
        reason = book.check_oco(bars, half)
        if reason:
            say(f"{format_local(now, tz)} [{v.name}] OCO virtual executou: {reason} | patrimônio virtual {num(book.equity)}")

    # 2) decisão a cada fechamento do candle de gatilho desta variante
    tf = v.cfg.estrategia.tf_gatilho
    last_close = floor_to_interval(now, tf) - 1  # fim do último candle de gatilho fechado
    if last_close <= book.last_decision_close_ms:
        return  # este candle já foi analisado
    view = book.view(now, (bid + ask) / 2, half)
    res = await analyze_live(v.cfg, secrets, client, conn, paper=view)
    d = res.decision
    sid = save_signal(
        conn, d, ratio=res.ratio, mode="paper", data_ages=res.data_ages, latency_ms=res.latency_ms, variant=v.name, market_extras=res.market_extras
    )
    book.mark_decided(last_close)
    t_bid, t_ask = res.trade_bid or bid, res.trade_ask or ask
    note = ""
    study = v.cfg.paper.estudo
    pos = book.open_trade()
    if d.action == "COMPRAR" and pos is None:
        tid = book.open(d, ratio=res.ratio, bid=t_bid, ask=t_ask, t_ms=now, signal_id=sid, bypassed={} if study else None)
        note = f" -> compra virtual #{tid}" if tid else ""
    elif study and pos is None and study_entry_ok(d):
        tid = book.open(d, ratio=res.ratio, bid=t_bid, ask=t_ask, t_ms=now, signal_id=sid, bypassed=d.strategy_gates)
        note = f" -> compra virtual de ESTUDO #{tid} (ignorou: {', '.join(d.strategy_gates) or 'nada'})" if tid else ""
    elif d.action == "VENDER" and pos is not None:
        bypassed = json.loads(pos["bypassed_json"] or "{}")
        if study and d.exit_reason == "invalidacao" and "tendencia" in bypassed:
            # entrou contra a tendência de propósito: sair por "tendência contra" anularia o teste. Stop/alvo/tempo seguem valendo.
            note = " -> estudo: mantém (entrou contra a tendência de propósito; stop virtual continua valendo)"
        else:
            pnl = book.close_market(bid=t_bid, ask=t_ask, t_ms=now, reason=d.exit_reason or "vender")
            note = f" -> venda virtual ({d.exit_reason}), resultado {num(pnl)}"
    why = d.blocks[0] if d.blocks else (d.reasons_for[0] if d.reasons_for else "")
    say(f"{format_local(now, tz)} [{v.name}] {d.action} {num(d.confidence, 0)}%{note} | sinal #{sid} | {why[:110]} | patrimônio virtual {num(book.equity)}")

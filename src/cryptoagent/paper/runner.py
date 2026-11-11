"""Laço do paper trading: a cada minuto confere a OCO virtual e, a cada fechamento do candle de
gatilho de cada variante, roda o MESMO motor ao vivo e registra a decisão.

- Dados de mercado reais, só leitura (API REST pública, com checagem de saúde a cada análise).
- Execução simulada: depois de decidir, espera `paper.atraso_execucao_s` e só então relê o melhor
  preço de compra/venda; a ordem virtual é executada nesse preço novo, com meio spread, slippage,
  taxa e as regras de tamanho mínimo do par.
- O replay usa este mesmo laço, com um cliente que serve o histórico e um relógio simulado.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from cryptoagent.config import Config, Secrets, apply_overlay
from cryptoagent.data.rest import BinanceError, BinanceReadOnlyClient
from cryptoagent.engine.decision import Decision, study_entry_ok
from cryptoagent.engine.journal import save_signal
from cryptoagent.engine.live import LiveResult, analyze_live
from cryptoagent.fmt import num, pct
from cryptoagent.paper.book import EntryRecord, PaperBook, PaperCosts
from cryptoagent.paper.filters import SymbolFilters, load_saved
from cryptoagent.security.redact import redact
from cryptoagent.storage import sim_db
from cryptoagent.timeutils import floor_to_interval, format_local, ms_to_local

log = logging.getLogger(__name__)


@dataclass
class Variant:
    """Uma variante do paper trading: nome, configuração e carteira virtual."""
    name: str
    cfg: Config
    book: PaperBook


def load_variants(cfg: Config, overlay_paths: list[str]) -> list[tuple[str, Config]]:
    """Configuração de cada variante: "padrao" é o config atual; as outras são arquivos de sobreposição aplicados SOBRE ele
    (inclusive sobre o --sobrepor e o --demo em uso)."""
    out = []
    for item in overlay_paths:
        if item == "padrao":
            out.append(("padrao", cfg))
        else:
            out.append((Path(item).stem, apply_overlay(cfg, cfg.path(item))))
    return out


async def load_filters(cfg: Config, client: BinanceReadOnlyClient, say: Callable[[str], None]) -> SymbolFilters | None:
    """Regras de tamanho de ordem do par: da Binance (exchangeInfo, público) ou, sem conexão, do arquivo salvo."""
    par = cfg.paper.par
    try:
        resp = await client.get("/api/v3/exchangeInfo", {"symbol": par})
        return SymbolFilters.from_exchange_info(resp.data, par)  # type: ignore[arg-type]
    except (BinanceError, ValueError, KeyError) as exc:
        saved = load_saved(cfg.path(cfg.paper.filtros_arquivo), par)
        say(f"[aviso] regras de ordem da Binance indisponíveis ({redact(exc)}); usando as salvas em {cfg.paper.filtros_arquivo}"
            if saved else f"[aviso] sem regras de tamanho de ordem para {par}: ordens virtuais não serão limitadas")
        return saved


def scenario(res: LiveResult, d: Decision, tz: str, setup_tf: str) -> dict:
    """Cenário de mercado no momento da decisão, para guardar junto com a operação virtual."""
    ctx = res.ctx
    local = ms_to_local(d.time_ms, tz)
    out = {"regime": d.regime, "hora_local": local.hour, "dia_semana": local.weekday(), "spread_pct": None, "funding_pct": None}
    if ctx is not None:
        m = ctx.market
        out.update(spread_pct=m.spread_pct, funding_pct=m.funding_rate, fator_horario=ctx.season.factor)
        try:
            out["atr_percentil_setup"] = float(m.last(setup_tf)["atr_pctl"])
        except (KeyError, IndexError, TypeError, ValueError):
            out["atr_percentil_setup"] = None
    for part in d.parts:
        out[f"parcela_{part.name}"] = part.value
    return out


async def run_paper(
    cfg: Config, secrets: Secrets, *, fee: float, capital: float | None = None, max_loss_brl: float | None = None,
    db_path: Path | None = None, client: BinanceReadOnlyClient | None = None, max_cycles: int | None = None,
    until_ms: int | None = None, say: Callable[[str], None] = print, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    adjust: Callable[[Config], Config] | None = None,
) -> None:
    """Laço do paper trading (ver a descrição do módulo).

    `capital` e `max_loss_brl` (R$) substituem os do config. `client` permite o replay (cliente com histórico e
    relógio simulado); sem ele, conecta na Binance (só leitura). Para depois de `max_cycles` minutos ou ao chegar
    em `until_ms`. `adjust` ajusta o config de cada variante (o replay desliga o perfil por horário).
    """
    pc = cfg.paper
    capital = capital if capital is not None else pc.capital
    max_loss = max_loss_brl if max_loss_brl is not None else pc.perda_max_dia_brl
    if max_loss >= capital:
        raise ValueError(f"a perda máxima do dia (R$ {max_loss:g}) precisa ser menor que o capital virtual (R$ {capital:g})")
    conn = sim_db.connect(db_path or cfg.path(pc.banco))
    costs = PaperCosts(fee, cfg.backtest.slippage_mercado_pct, cfg.backtest.slippage_stop_pct)
    own_client = client is None
    client = client or BinanceReadOnlyClient(cfg.binance, secrets)
    try:
        await client.sync_time()
        filters = await load_filters(cfg, client, say)
        start = client.server_now_ms()
        variants = [
            Variant(name, vcfg, PaperBook(conn, name, pc.par, capital=capital, max_loss_brl=max_loss, divisor=cfg.risco.divisor_perda_diaria,
                                          costs=costs, tz=cfg.fuso.local, day_start=cfg.fuso.inicio_dia_local, now_ms=start, filters=filters))
            for name, vcfg in ((n, adjust(c) if adjust else c) for n, c in load_variants(cfg, pc.variantes))
        ]
        for v in variants:
            if abs(v.book.initial_capital - capital) > 1e-9:
                say(f"[aviso] [{v.name}] a carteira virtual já existe com capital inicial R$ {num(v.book.initial_capital)}; "
                    f"o valor pedido (R$ {num(capital)}) só vale depois de: cryptoagent paper resetar")
        lim = f"mínimo R$ {num(filters.min_notional)} por ordem, lote {filters.step:g}" if filters else "sem limites de ordem"
        say(
            f"Paper trading em {pc.par} com {len(variants)} variante(s): {', '.join(v.name for v in variants)}. Capital virtual R$ {num(capital)}; "
            f"perda máxima do dia R$ {num(max_loss)}; taxa {pct(fee * 100, 3)}/lado; atraso de execução {pc.atraso_execucao_s:g} s; {lim}. "
            "Nenhuma ordem real é enviada."
        )
        cycles = 0
        while True:
            await client.sync_time()
            now = client.server_now_ms()
            if until_ms is not None and now >= until_ms:
                break
            try:
                bt = await client.get("/api/v3/ticker/bookTicker", {"symbol": pc.par})
                bid, ask = float(bt.data["bidPrice"]), float(bt.data["askPrice"])  # type: ignore[index]
                half = (ask - bid) / (ask + bid)
                for v in variants:
                    await _cycle(v, cfg, secrets, client, conn, now, bid, ask, half, say, sleep)
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
        if own_client:
            await client.aclose()


async def _fresh_quote(client: BinanceReadOnlyClient, symbol: str, sleep: Callable[[float], Awaitable[None]], delay_s: float) -> tuple[float, float, int]:
    """Espera o atraso de execução e relê o melhor preço. Devolve (bid, ask, instante da execução)."""
    if delay_s > 0:
        await sleep(delay_s)
    r = await client.get("/api/v3/ticker/bookTicker", {"symbol": symbol})
    return float(r.data["bidPrice"]), float(r.data["askPrice"]), client.server_now_ms()  # type: ignore[index]


async def _cycle(
    v: Variant, cfg: Config, secrets: Secrets, client: BinanceReadOnlyClient, conn: sqlite3.Connection, now: int, bid: float, ask: float,
    half: float, say: Callable[[str], None], sleep: Callable[[float], Awaitable[None]],
) -> None:
    pc, book, tz = cfg.paper, v.book, cfg.fuso.local
    # 1) OCO virtual nos candles de 1m fechados desde a última verificação
    pos = book.open_trade()
    if pos is not None:
        bars, _ = await client.klines(pc.par, "1m", start_ms=int(pos["checked_until_ms"]), limit=1000)
        bars = bars[bars["close_time"] < now]
        reason = book.check_oco(bars, half)
        if reason:
            say(f"{format_local(now, tz)} [{v.name}] OCO virtual executou: {reason} | patrimônio virtual R$ {num(book.equity)}")

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
    decision_mid = ((res.trade_bid or bid) + (res.trade_ask or ask)) / 2
    note = ""
    study = v.cfg.paper.estudo
    pos = book.open_trade()
    buy_for_study = study and pos is None and d.action != "COMPRAR" and study_entry_ok(d)
    if pos is None and (d.action == "COMPRAR" or buy_for_study):
        features = None
        if res.ctx is not None:
            from cryptoagent.model.features import extract_features

            try:
                features = extract_features(res.ctx, d, v.cfg.estrategia, tz)
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                log.warning("Variáveis do momento indisponíveis: %s", exc)
        record = EntryRecord(
            decision_ms=d.time_ms, decision_price=decision_mid, signal_id=sid,
            reason="; ".join(d.reasons_for[:4]) or (d.trigger.detail if d.trigger else ""),
            features=features, scenario=scenario(res, d, tz, v.cfg.estrategia.tf_setup),
            bypassed=(d.strategy_gates if buy_for_study else {}) if study else None,
        )
        e_bid, e_ask, e_ms = await _fresh_quote(client, pc.par, sleep, pc.atraso_execucao_s)
        tid, why = book.open(d, ratio=res.ratio, bid=e_bid, ask=e_ask, t_ms=e_ms, record=record)
        kind = "compra virtual de ESTUDO" if buy_for_study else "compra virtual"
        extra = f" (ignorou: {', '.join(d.strategy_gates) or 'nada'})" if buy_for_study else ""
        note = f" -> {kind} #{tid}{extra}" if tid else f" -> compra NÃO executada: {why}"
    elif d.action == "VENDER" and pos is not None:
        bypassed = json.loads(pos["bypassed_json"] or "{}")
        if study and d.exit_reason == "invalidacao" and "tendencia" in bypassed:
            # entrou contra a tendência de propósito: sair por "tendência contra" anularia o teste. Stop/alvo/tempo seguem valendo.
            note = " -> estudo: mantém (entrou contra a tendência de propósito; stop virtual continua valendo)"
        else:
            x_bid, x_ask, x_ms = await _fresh_quote(client, pc.par, sleep, pc.atraso_execucao_s)
            pnl = book.close_market(bid=x_bid, ask=x_ask, t_ms=x_ms, reason=d.exit_reason or "vender", decision_ms=d.time_ms)
            note = f" -> venda virtual ({d.exit_reason}), resultado R$ {num(pnl)}"
    why = d.blocks[0] if d.blocks else (d.reasons_for[0] if d.reasons_for else "")
    say(f"{format_local(now, tz)} [{v.name}] {d.action} {num(d.confidence, 0)}%{note} | sinal #{sid} | {why[:110]} | patrimônio virtual R$ {num(book.equity)}")

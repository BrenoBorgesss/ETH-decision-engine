"""Monta o DecisionContext com dados AO VIVO e chama o mesmo decide() do backtest.

Também mede a idade de cada dado e a latência total, que vão junto com cada sinal.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import dataclass, field

import pandas as pd

from cryptoagent.account.status import AccountMonitor, AccountStatus
from cryptoagent.config import Config, Secrets
from cryptoagent.data import history
from cryptoagent.data.health import Check, HealthReport, check_age, check_candle, check_clock
from cryptoagent.data.rest import BinanceError, BinanceReadOnlyClient
from cryptoagent.engine.context import DecisionContext, MarketView, PositionView, RiskView, SeasonView
from cryptoagent.engine.decision import Decision, decide
from cryptoagent.engine.journal import auto_associate, levels_for_open_lots
from cryptoagent.indicators.compute import compute_indicators
from cryptoagent.indicators.structure import previous_day_levels, support_resistance
from cryptoagent.indicators.volume import book_imbalance
from cryptoagent.seasonality.analysis import load_profiles
from cryptoagent.seasonality.compare import candle_vs_same_hour, context_multiplier
from cryptoagent.seasonality.cost_model import build_cost_model
from cryptoagent.seasonality.profiles import add_buckets
from cryptoagent.security.redact import redact
from cryptoagent.timeutils import now_ms


@dataclass(frozen=True)
class PaperView:
    """Carteira VIRTUAL do paper trading, usada no lugar da conta real."""

    trade_symbol: str
    fee: float
    capital: float
    risk_per_trade: float
    daily_limit_reason: str | None = None
    # posição virtual aberta, com preços no par de OPERAÇÃO
    qty: float | None = None
    entry_price: float | None = None
    opened_ms: int | None = None
    stop: float | None = None
    target1: float | None = None
    target2: float | None = None
    source: str = "paper"


@dataclass
class LiveResult:
    decision: Decision
    ctx: DecisionContext | None
    account: AccountStatus | None
    health: HealthReport
    ratio: float
    data_ages: dict[str, float] = field(default_factory=dict)
    latency_ms: float = 0.0
    hour_note: str = ""
    trade_bid: float | None = None  # melhor compra/venda do par de operação no momento da análise
    trade_ask: float | None = None

    @property
    def market_extras(self) -> dict:
        m = self.ctx.market if self.ctx else None
        return {"book_imbalance": m.book_imbalance, "funding_pct": m.funding_rate, "spread_pct": m.spread_pct} if m else {}


async def analyze_live(
    cfg: Config,
    secrets: Secrets,
    client: BinanceReadOnlyClient,
    conn: sqlite3.Connection,
    *,
    paper: PaperView | None = None,
    candles: dict[str, pd.DataFrame] | None = None,
) -> LiveResult:
    """Análise ao vivo. Com `paper`, usa a carteira virtual em vez da conta real (a conta nem é lida).

    `candles` (opcional): candles já recebidos pelo WebSocket, por timeframe. Os timeframes que
    faltarem são buscados pela API REST (fallback).
    """
    t0 = time.perf_counter()
    p, sd, tz = cfg.estrategia, cfg.saude_dados, cfg.fuso.local
    sym = cfg.mercado.simbolo_analise
    health = HealthReport()
    ages: dict[str, float] = {}

    offset, _ = await client.sync_time()
    health.add(check_clock(offset, sd.desvio_relogio_max_ms))

    if paper is None:
        account = await AccountMonitor(cfg, secrets, client, conn).refresh()  # pode levantar ExcessivePermissions
        trade_sym = (account.session.par_operacao if account.session else None) or cfg.mercado.par_operacao or sym
    else:
        account = None
        trade_sym = paper.trade_symbol

    # ---- candles + indicadores de cada timeframe (buscados em paralelo)
    frames: dict[str, pd.DataFrame] = {}
    server_now = client.server_now_ms()
    candles = candles or {}
    rest_tfs = [tf for tf in p.timeframes if tf not in candles]
    limits, default_limit = cfg.binance.candles_por_consulta, cfg.binance.candles_por_consulta_padrao
    fetched = await asyncio.gather(*(client.klines(sym, tf, limit=limits.get(tf, default_limit)) for tf in rest_tfs))
    sources = {tf: (df, r.source) for tf, (df, r) in zip(rest_tfs, fetched)}
    sources.update({tf: (df, "binance-ws:kline") for tf, df in candles.items() if tf in p.timeframes})
    for tf in p.timeframes:
        df, source = sources[tf]
        df = df[df["close_time"] < server_now].reset_index(drop=True)
        frames[tf] = compute_indicators(df, cfg.indicadores, cfg.regime, tz_name=tz, day_start=cfg.fuso.inicio_dia_local)
        last_close = int(df["close_time"].iloc[-1]) if not df.empty else None
        c = check_candle(f"candles {tf}", last_close, tf, server_now, sd.candle_tolerancia_s, source)
        health.add(c)
        ages[f"candle_{tf}_s"] = c.age_s if c.age_s is not None else -1

    # ---- preços agora: par de análise e par de operação
    bt = await client.get("/api/v3/ticker/bookTicker", {"symbol": sym})
    a_bid, a_ask = float(bt.data["bidPrice"]), float(bt.data["askPrice"])  # type: ignore[index]
    health.add(check_age(f"preço {sym}", bt.received_ms, now_ms(), sd.preco_max_s, bt.source))
    ages["preco_analise_s"] = (now_ms() - bt.received_ms) / 1000
    if trade_sym != sym:
        bt2 = await client.get("/api/v3/ticker/bookTicker", {"symbol": trade_sym})
        t_bid, t_ask = float(bt2.data["bidPrice"]), float(bt2.data["askPrice"])  # type: ignore[index]
        health.add(check_age(f"preço {trade_sym}", bt2.received_ms, now_ms(), sd.preco_max_s, bt2.source))
        ages["preco_operacao_s"] = (now_ms() - bt2.received_ms) / 1000
    else:
        t_bid, t_ask = a_bid, a_ask
    a_mid, t_mid = (a_bid + a_ask) / 2, (t_bid + t_ask) / 2
    ratio = t_mid / a_mid
    spread_pct = (t_ask - t_bid) / t_mid * 100

    depth = await client.get("/api/v3/depth", {"symbol": sym, "limit": 20})
    imbalance = book_imbalance([(float(a), float(b)) for a, b in depth.data["bids"]], [(float(a), float(b)) for a, b in depth.data["asks"]])  # type: ignore[index]

    funding = None
    try:
        pi = await client.get("/fapi/v1/premiumIndex", {"symbol": sym})
        funding = float(pi.data["lastFundingRate"]) * 100  # type: ignore[index]
        health.add(check_age("derivativos", pi.received_ms, now_ms(), sd.derivativos_max_s, pi.source, critical=False))
    except BinanceError as exc:
        health.add(Check("derivativos", False, redact(exc), critical=False))

    # ---- suportes/resistências no setup
    s15 = frames[p.tf_setup]
    pos_idx = len(s15) - 1
    levels = []
    if pos_idx >= 0 and s15["atr"].notna().iloc[-1]:
        levels = support_resistance(
            s15, s15, at_pos=pos_idx, atr_value=float(s15["atr"].iloc[-1]), tolerance_atr=cfg.indicadores.sr_tolerancia_atr,
            lookback=cfg.indicadores.sr_janela_barras, max_levels=cfg.indicadores.sr_max_niveis,
        ) + previous_day_levels(s15, pos_idx, tz, cfg.fuso.inicio_dia_local, days=s15["trading_day"])

    # ---- custo de ida e volta no par de operação (taxa real da conta quando houver)
    if paper is not None:
        fee = paper.fee
    else:
        fee = account.exit_fee_rate if account.exit_fee_rate is not None else cfg.custos.taxa_padrao_taker
    cost = build_cost_model(trade_sym, fee, cfg.sazonalidade.spread_padrao_pct, cfg.path(cfg.custos.banco))

    # ---- análise por horário (só padrões validados mexem na confiança)
    season = SeasonView()
    hour_note = ""
    profiles = load_profiles(cfg.path(cfg.sazonalidade.relatorio_pasta) / "sazonalidade.json")
    if trade_sym in profiles:
        store = history.HistoryStore(cfg.path(cfg.historico.pasta))
        tf_s = cfg.sazonalidade.timeframe
        try:
            await history.update_from_rest(store, client, trade_sym, tf_s, months=cfg.historico.meses, now=server_now)
            hist = store.load(trade_sym, tf_s)
            hist = hist[hist["open_time"] >= int(hist["open_time"].iloc[-1]) - (cfg.sazonalidade.dias_comparacao + 2) * 86_400_000]
            hb = add_buckets(hist, tz, cfg.indicadores.atr)
            comp = candle_vs_same_hour(hb, cfg.sazonalidade.dias_comparacao, cfg.sazonalidade.percentil_baixo, cfg.sazonalidade.percentil_alto)
            hour = int(hb["hour"].iloc[-1])
            hrow = next(h for h in profiles[trade_sym]["hours"] if h["hour"] == hour)
            ctxf = context_multiplier(hrow["class"], hrow["validated"], comp.label, cfg.sazonalidade)
            season = SeasonView(ctxf.factor, ctxf.reasons)
            label = {"compensa": "compensa", "neutro": "neutro", "nao_compensa": "não compensa"}[hrow["class"]]
            hour_note = f"{hour:02d}h: {label} ({'padrão validado' if hrow['validated'] else 'padrão não validado'}); movimento agora {comp.label}"
        except (BinanceError, StopIteration, IndexError) as exc:
            health.add(Check("análise por horário", False, f"indisponível: {redact(exc)}", critical=False))
    else:
        health.add(Check("análise por horário", False, "sem perfil (rode: cryptoagent sazonalidade analisar)", critical=False))

    # ---- posição da operação (traduzida para o par de análise)
    position = None
    if paper is not None:
        if paper.qty:
            position = PositionView(
                qty=paper.qty, avg_cost=paper.entry_price / ratio, opened_ms=paper.opened_ms or server_now,
                stop=paper.stop / ratio if paper.stop else None, target1=paper.target1 / ratio if paper.target1 else None,
                target2=paper.target2 / ratio if paper.target2 else None, source=paper.source,
            )
        risk = RiskView(
            blocked_reasons=health.problems, risk_per_trade_brl=paper.risk_per_trade, capital_brl=paper.capital,
            data_warnings=health.warnings, daily_limit_reason=paper.daily_limit_reason,
        )
        market = MarketView(server_now, a_mid, frames, levels, spread_pct, imbalance, funding)
        ctx = DecisionContext(sym, trade_sym, ratio, market, risk, cost.breakeven_pct, position, season, live=True)
        decision = decide(ctx, p, spread_max_pct=cfg.custos.spread_max_pct)
        return LiveResult(decision, ctx, None, health, ratio, ages, (time.perf_counter() - t0) * 1000, hour_note, t_bid, t_ask)

    if account.day is not None and account.day.open_qty > 1e-9:
        auto_associate(conn, trade_sym)
        open_ids = [tid for tid, _, _ in account.day.open_lots]
        assoc = levels_for_open_lots(conn, trade_sym, open_ids)
        stop = t1 = t2 = None
        source = "regras (sem sinal associado)"
        if assoc:
            stop, t1, t2 = (x / ratio for x in assoc[:3])
            source = assoc[3]
        position = PositionView(
            qty=account.day.open_qty,
            avg_cost=(account.day.avg_cost_real or account.bid or t_mid) / ratio,
            opened_ms=account.day.opened_ms or server_now,
            stop=stop,
            target1=t1,
            target2=t2,
            source=source,
        )

    limit_reason = next((r for r in account.reasons if r.startswith("PERDA MÁXIMA")), None)
    blocked = [r for r in account.reasons if r != limit_reason] + health.problems
    risk = RiskView(
        blocked_reasons=blocked,
        risk_per_trade_brl=account.limits.risk_per_trade_brl if account.limits else None,
        capital_brl=account.effective_capital_brl,
        data_warnings=health.warnings,
        daily_limit_reason=limit_reason,
    )
    market = MarketView(server_now, a_mid, frames, levels, spread_pct, imbalance, funding)
    ctx = DecisionContext(sym, trade_sym, ratio, market, risk, cost.breakeven_pct, position, season, live=True)
    decision = decide(ctx, p, spread_max_pct=cfg.custos.spread_max_pct)
    ages["saldo_s"] = (now_ms() - account.balances_ms) / 1000 if account.balances_ms else -1
    return LiveResult(decision, ctx, account, health, ratio, ages, (time.perf_counter() - t0) * 1000, hour_note, t_bid, t_ask)

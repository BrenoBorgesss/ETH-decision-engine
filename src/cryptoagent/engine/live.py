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
from cryptoagent.data.health import Check, HealthReport, check_age, check_candle, check_clock, check_quality
from cryptoagent.data.rest import ApiResponse, BinanceError, BinanceReadOnlyClient
from cryptoagent.data.validation import inspect_klines
from cryptoagent.engine.context import DecisionContext, MarketView, PositionView, RiskView, SeasonView
from cryptoagent.engine.decision import Decision, decide
from cryptoagent.engine.journal import auto_associate, levels_for_open_lots
from cryptoagent.engine.reliability import check_reliability
from cryptoagent.indicators.cache import IndicatorCache
from cryptoagent.indicators.structure import Level, previous_day_levels, support_resistance
from cryptoagent.indicators.volume import book_imbalance
from cryptoagent.learning.panorama import MomentView
from cryptoagent.learning.state import apply_model_champion, champion_config, champion_metrics, load_state, moment_view
from cryptoagent.seasonality.analysis import load_profiles
from cryptoagent.seasonality.compare import candle_vs_same_hour, context_multiplier
from cryptoagent.seasonality.cost_model import build_cost_model
from cryptoagent.seasonality.profiles import add_buckets
from cryptoagent.security.redact import redact
from cryptoagent.timeutils import ms_to_local


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
    """Resultado de uma análise ao vivo: decisão, contexto, conta, saúde dos dados, idades e latência."""

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
    moment: MomentView | None = None  # panorama do cenário de agora (aprendizado contínuo)
    champion: str = ""  # versão campeã usada na decisão

    @property
    def market_extras(self) -> dict:
        """Desequilíbrio do livro, funding e spread do momento, para gravar junto com o sinal."""
        m = self.ctx.market if self.ctx else None
        return (
            {"book_imbalance": m.book_imbalance, "funding_pct": m.funding_rate, "spread_pct": m.spread_pct} if m else {}
        )


# Indicadores já calculados por (par, timeframe): reaproveitados enquanto os candles fechados não mudam.
INDICATOR_CACHE = IndicatorCache()


async def _fetch_candles(
    cfg: Config, client: BinanceReadOnlyClient, sym: str, candles: dict[str, pd.DataFrame]
) -> dict[str, tuple[pd.DataFrame, str]]:
    """Candles de cada timeframe da estratégia, com a fonte: os do WebSocket que vieram prontos e o resto pela REST, em paralelo."""
    tfs = cfg.estrategia.timeframes
    rest_tfs = [tf for tf in tfs if tf not in candles]
    limits, default_limit = cfg.binance.candles_por_consulta, cfg.binance.candles_por_consulta_padrao
    fetched = await asyncio.gather(*(client.klines(sym, tf, limit=limits.get(tf, default_limit)) for tf in rest_tfs))
    sources = {tf: (df, r.source) for tf, (df, r) in zip(rest_tfs, fetched, strict=True)}
    sources.update({tf: (df, "binance-ws:kline") for tf, df in candles.items() if tf in tfs})
    return sources


async def _fetch_funding(client: BinanceReadOnlyClient, sym: str) -> ApiResponse | BinanceError:
    """Funding do par nos futuros. Devolve o erro em vez de levantá-lo: derivativos não podem derrubar a análise."""
    try:
        return await client.get("/fapi/v1/premiumIndex", {"symbol": sym})
    except BinanceError as exc:
        return exc


def _prepare_frames(
    cfg: Config,
    sym: str,
    sources: dict[str, tuple[pd.DataFrame, str]],
    server_now: int,
    health: HealthReport,
    ages: dict[str, float],
) -> dict[str, pd.DataFrame]:
    """Fica só com candles FECHADOS, valida, calcula os indicadores (com cache) e registra a saúde de cada timeframe."""
    frames: dict[str, pd.DataFrame] = {}
    for tf in cfg.estrategia.timeframes:
        df, source = sources[tf]
        df = df[df["close_time"] < server_now].reset_index(drop=True)
        quality = inspect_klines(df, tf, now_ms=server_now)
        if not quality.ok:  # candle inválido é problema crítico: o sinal vira ESPERAR
            health.add(check_quality(f"qualidade dos candles {tf}", quality, source))
        frames[tf] = INDICATOR_CACHE.compute(
            (sym, tf), df, cfg.indicadores, cfg.regime, tz_name=cfg.fuso.local, day_start=cfg.fuso.inicio_dia_local
        )
        last_close = int(df["close_time"].iloc[-1]) if not df.empty else None
        c = check_candle(f"candles {tf}", last_close, tf, server_now, cfg.saude_dados.candle_tolerancia_s, source)
        health.add(c)
        ages[f"candle_{tf}_s"] = c.age_s if c.age_s is not None else -1
    return frames


@dataclass(frozen=True)
class _Quotes:
    """Preços de agora no par de análise e no de operação, e o desequilíbrio do livro."""

    a_bid: float
    a_ask: float
    t_bid: float
    t_ask: float
    imbalance: float | None

    @property
    def a_mid(self) -> float:
        """Preço médio no par de análise."""
        return (self.a_bid + self.a_ask) / 2

    @property
    def t_mid(self) -> float:
        """Preço médio no par de operação."""
        return (self.t_bid + self.t_ask) / 2

    @property
    def ratio(self) -> float:
        """Preço do par de operação ÷ preço do par de análise."""
        return self.t_mid / self.a_mid

    @property
    def spread_pct(self) -> float:
        """Spread do par de operação, em % do preço médio."""
        return (self.t_ask - self.t_bid) / self.t_mid * 100


async def _fetch_quotes(
    cfg: Config, client: BinanceReadOnlyClient, sym: str, trade_sym: str, health: HealthReport, ages: dict[str, float]
) -> _Quotes:
    """Busca em paralelo o melhor preço dos dois pares e o livro, e registra a idade de cada preço."""
    sd = cfg.saude_dados
    wanted = [
        client.get("/api/v3/ticker/bookTicker", {"symbol": sym}),
        client.get("/api/v3/depth", {"symbol": sym, "limit": 20}),
    ]
    if trade_sym != sym:
        wanted.append(client.get("/api/v3/ticker/bookTicker", {"symbol": trade_sym}))
    bt, depth, *rest = await asyncio.gather(*wanted)
    a_bid, a_ask = float(bt.data["bidPrice"]), float(bt.data["askPrice"])  # type: ignore[index]
    health.add(check_age(f"preço {sym}", bt.received_ms, client.local_now_ms(), sd.preco_max_s, bt.source))
    ages["preco_analise_s"] = (client.local_now_ms() - bt.received_ms) / 1000
    if rest:
        bt2 = rest[0]
        t_bid, t_ask = float(bt2.data["bidPrice"]), float(bt2.data["askPrice"])  # type: ignore[index]
        health.add(check_age(f"preço {trade_sym}", bt2.received_ms, client.local_now_ms(), sd.preco_max_s, bt2.source))
        ages["preco_operacao_s"] = (client.local_now_ms() - bt2.received_ms) / 1000
    else:
        t_bid, t_ask = a_bid, a_ask
    imbalance = book_imbalance(
        [(float(a), float(b)) for a, b in depth.data["bids"]], [(float(a), float(b)) for a, b in depth.data["asks"]]
    )  # type: ignore[index]
    return _Quotes(a_bid, a_ask, t_bid, t_ask, imbalance)


def _structure_levels(cfg: Config, frames: dict[str, pd.DataFrame]) -> list[Level]:
    """Suportes e resistências do timeframe de setup, mais os níveis do dia anterior."""
    p, tz = cfg.estrategia, cfg.fuso.local
    s15 = frames[p.tf_setup]
    pos_idx = len(s15) - 1
    levels = []
    if pos_idx >= 0 and s15["atr"].notna().iloc[-1]:
        levels = support_resistance(
            s15,
            s15,
            at_pos=pos_idx,
            atr_value=float(s15["atr"].iloc[-1]),
            tolerance_atr=cfg.indicadores.sr_tolerancia_atr,
            lookback=cfg.indicadores.sr_janela_barras,
            max_levels=cfg.indicadores.sr_max_niveis,
        ) + previous_day_levels(s15, pos_idx, tz, cfg.fuso.inicio_dia_local, days=s15["trading_day"])
    return levels


async def _season_context(
    cfg: Config, client: BinanceReadOnlyClient, trade_sym: str, server_now: int, health: HealthReport
) -> tuple[SeasonView, str]:
    """Efeito do horário na confiança (só padrões validados) e a nota em texto. Problemas viram aviso não crítico."""
    tz = cfg.fuso.local
    season = SeasonView()
    hour_note = ""
    profiles = load_profiles(cfg.path(cfg.sazonalidade.relatorio_pasta) / "sazonalidade.json")
    if trade_sym in profiles:
        store = history.HistoryStore(cfg.path(cfg.historico.pasta))
        tf_s = cfg.sazonalidade.timeframe
        try:
            await history.update_from_rest(store, client, trade_sym, tf_s, months=cfg.historico.meses, now=server_now)
            hist = store.load(trade_sym, tf_s)
            hist = hist[
                hist["open_time"]
                >= int(hist["open_time"].iloc[-1]) - (cfg.sazonalidade.dias_comparacao + 2) * 86_400_000
            ]
            hb = add_buckets(hist, tz, cfg.indicadores.atr)
            comp = candle_vs_same_hour(
                hb, cfg.sazonalidade.dias_comparacao, cfg.sazonalidade.percentil_baixo, cfg.sazonalidade.percentil_alto
            )
            hour = int(hb["hour"].iloc[-1])
            hrow = next(h for h in profiles[trade_sym]["hours"] if h["hour"] == hour)
            ctxf = context_multiplier(hrow["class"], hrow["validated"], comp.label, cfg.sazonalidade)
            season = SeasonView(ctxf.factor, ctxf.reasons)
            label = {"compensa": "compensa", "neutro": "neutro", "nao_compensa": "não compensa"}[hrow["class"]]
            hour_note = f"{hour:02d}h: {label} ({'padrão validado' if hrow['validated'] else 'padrão não validado'}); movimento agora {comp.label}"
        except (BinanceError, StopIteration, IndexError) as exc:
            health.add(Check("análise por horário", False, f"indisponível: {redact(exc)}", critical=False))
    else:
        health.add(
            Check("análise por horário", False, "sem perfil (rode: cryptoagent sazonalidade analisar)", critical=False)
        )
    return season, hour_note


async def analyze_live(
    cfg: Config,
    secrets: Secrets,
    client: BinanceReadOnlyClient,
    conn: sqlite3.Connection,
    *,
    paper: PaperView | None = None,
    candles: dict[str, pd.DataFrame] | None = None,
    use_champion: bool = True,
) -> LiveResult:
    """Análise ao vivo. Com `paper`, usa a carteira virtual em vez da conta real (a conta nem é lida).

    `candles` (opcional): candles já recebidos pelo WebSocket, por timeframe. Os timeframes que
    faltarem são buscados pela API REST (fallback).
    `use_champion`: decide com a estratégia campeã do aprendizado contínuo (as variantes de estudo do
    paper passam False, porque testam a própria configuração).
    """
    t0 = time.perf_counter()
    learning = load_state(cfg)
    if use_champion:
        cfg = champion_config(cfg, learning)
    p, sd = cfg.estrategia, cfg.saude_dados
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

    # ---- 1ª leva, em paralelo: candles que não vieram do WebSocket e derivativos
    server_now = client.server_now_ms()
    sources, funding_resp = await asyncio.gather(
        _fetch_candles(cfg, client, sym, candles or {}), _fetch_funding(client, sym)
    )
    frames = _prepare_frames(cfg, sym, sources, server_now, health, ages)

    # ---- 2ª leva, em paralelo e por último, para chegarem frescos: preços (análise e operação) e livro
    q = await _fetch_quotes(cfg, client, sym, trade_sym, health, ages)
    a_mid, t_mid, t_bid, t_ask, ratio, spread_pct, imbalance = (
        q.a_mid,
        q.t_mid,
        q.t_bid,
        q.t_ask,
        q.ratio,
        q.spread_pct,
        q.imbalance,
    )

    funding = None
    if isinstance(funding_resp, BinanceError):  # derivativos são só contexto: falha vira aviso, não bloqueia
        health.add(Check("derivativos", False, redact(funding_resp), critical=False))
    else:
        funding = float(funding_resp.data["lastFundingRate"]) * 100  # type: ignore[index]
        health.add(
            check_age(
                "derivativos",
                funding_resp.received_ms,
                client.local_now_ms(),
                sd.derivativos_max_s,
                funding_resp.source,
                critical=False,
            )
        )

    levels = _structure_levels(cfg, frames)

    # ---- custo de ida e volta no par de operação (taxa real da conta quando houver)
    if paper is not None:
        fee = paper.fee
    else:
        fee = account.exit_fee_rate if account.exit_fee_rate is not None else cfg.custos.taxa_padrao_taker
    cost = build_cost_model(trade_sym, fee, cfg.sazonalidade.spread_padrao_pct, cfg.path(cfg.custos.banco))

    season, hour_note = await _season_context(cfg, client, trade_sym, server_now, health)

    # ---- aprendizado contínuo: cenário pouco visto e mudança de regime detectada no último ciclo
    setup = frames[p.tf_setup]
    local = ms_to_local(server_now, cfg.fuso.local)
    moment = moment_view(
        str(setup["regime"].iloc[-1]) if len(setup) else None,
        local.hour + local.minute / 60,
        float(setup["atr_pctl"].iloc[-1]) if len(setup) and pd.notna(setup["atr_pctl"].iloc[-1]) else None,
        cfg,
        learning,
    )
    novelty = SeasonView(moment.factor, [moment.reason] if moment.reason else [])
    if learning.drift_summary:
        health.add(
            Check(
                "estabilidade dos dados",
                False,
                f"mudança de regime no último ciclo de aprendizado ({learning.drift_summary})",
                critical=False,
            )
        )
    champion_name = (
        f"v{learning.champion.id} {learning.champion.name}" if learning.champion else "regras (config atual)"
    )

    # ---- posição e risco: carteira virtual (paper) ou conta real
    if paper is not None:
        position, risk = _paper_position_and_risk(paper, ratio, server_now, health)
    else:
        position = _real_position(conn, account, trade_sym, ratio, t_mid, server_now)
        risk = _real_risk(cfg, client, account, trade_sym, health, champion_metrics(learning) if use_champion else None)
    market = MarketView(server_now, a_mid, frames, levels, spread_pct, imbalance, funding)
    ctx = DecisionContext(
        sym, trade_sym, ratio, market, risk, cost.breakeven_pct, position, season, live=True, novelty=novelty
    )
    decision = decide(ctx, p, spread_max_pct=cfg.custos.spread_max_pct)
    if use_champion:
        decision = apply_model_champion(decision, ctx, cfg, learning)
    if account is not None:
        ages["saldo_s"] = (client.local_now_ms() - account.balances_ms) / 1000 if account.balances_ms else -1
    return LiveResult(
        decision,
        ctx,
        account,
        health,
        ratio,
        ages,
        (time.perf_counter() - t0) * 1000,
        hour_note,
        t_bid,
        t_ask,
        moment,
        champion_name,
    )


def _paper_position_and_risk(
    paper: PaperView, ratio: float, server_now: int, health: HealthReport
) -> tuple[PositionView | None, RiskView]:
    """Posição e risco da carteira VIRTUAL, com os preços traduzidos para o par de análise."""
    position = None
    if paper.qty:
        position = PositionView(
            qty=paper.qty,
            avg_cost=paper.entry_price / ratio,
            opened_ms=paper.opened_ms or server_now,
            stop=paper.stop / ratio if paper.stop else None,
            target1=paper.target1 / ratio if paper.target1 else None,
            target2=paper.target2 / ratio if paper.target2 else None,
            source=paper.source,
        )
    risk = RiskView(
        blocked_reasons=health.problems,
        risk_per_trade_brl=paper.risk_per_trade,
        capital_brl=paper.capital,
        data_warnings=health.warnings,
        daily_limit_reason=paper.daily_limit_reason,
    )
    return position, risk


def _real_position(
    conn: sqlite3.Connection, account: AccountStatus, trade_sym: str, ratio: float, t_mid: float, server_now: int
) -> PositionView | None:
    """Posição da operação na conta real (pelo histórico de operações, conferido com o saldo), com stop e alvos do sinal associado."""
    if account.day is None or account.day.open_qty <= 1e-9:
        return None
    auto_associate(conn, trade_sym)
    open_ids = [tid for tid, _, _ in account.day.open_lots]
    assoc = levels_for_open_lots(conn, trade_sym, open_ids)
    stop = t1 = t2 = None
    source = "regras (sem sinal associado)"
    if assoc:
        stop, t1, t2 = (x / ratio for x in assoc[:3])
        source = assoc[3]
    return PositionView(
        qty=account.day.open_qty,
        avg_cost=(account.day.avg_cost_real or account.bid or t_mid) / ratio,
        opened_ms=account.day.opened_ms or server_now,
        stop=stop,
        target1=t1,
        target2=t2,
        source=source,
    )


def _real_risk(
    cfg: Config,
    client: BinanceReadOnlyClient,
    account: AccountStatus,
    trade_sym: str,
    health: HealthReport,
    champion: dict | None,
) -> RiskView:
    """Risco da conta real: travas da conta e dos dados, perda máxima do dia (à parte: ainda permite vender) e confiabilidade."""
    limit_reason = next((r for r in account.reasons if r.startswith("PERDA MÁXIMA")), None)
    blocked = [r for r in account.reasons if r != limit_reason] + health.problems
    return RiskView(
        blocked_reasons=blocked,
        risk_per_trade_brl=account.limits.risk_per_trade_brl if account.limits else None,
        capital_brl=account.effective_capital_brl,
        data_warnings=health.warnings,
        daily_limit_reason=limit_reason,
        validation_reason=check_reliability(cfg, trade_sym, client.local_now_ms(), champion=champion).block_reason,
    )

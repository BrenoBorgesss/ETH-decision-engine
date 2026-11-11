"""Travas, filtro de tendência dos timeframes maiores e cálculo dos níveis (entrada, stop, alvos)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from cryptoagent.config import Estrategia
from cryptoagent.engine.context import DecisionContext
from cryptoagent.fmt import num
from cryptoagent.indicators.structure import nearest_levels

REQUIRED_COLUMNS = {
    "contexto": ["close", "ema_50", "ema_200", "ema_50_slope"],
    "confirmacao": ["close", "ema_50", "adx", "plus_di", "minus_di", "structure", "regime"],
    "setup": ["close", "vwap", "atr", "dist_vwap_atr", "bb_pctb", "bb_mid", "bb_upper", "rsi", "regime"],
    "gatilho": ["close", "low", "bb_pctb", "bb_lower", "atr", "rsi"],
}


def data_problems(ctx: DecisionContext, p: Estrategia) -> list[str]:
    """Falta de candles ou indicador ainda indefinido (aquecimento) = ESPERAR."""
    problems = []
    tfs = {"contexto": p.tf_contexto, "confirmacao": p.tf_confirmacao, "setup": p.tf_setup, "gatilho": p.tf_gatilho}
    for role, tf in tfs.items():
        frame = ctx.market.frames.get(tf)
        if frame is None or frame.empty:
            problems.append(f"sem candles de {tf}")
            continue
        last = frame.iloc[-1]
        missing = [c for c in REQUIRED_COLUMNS[role] if c not in frame.columns or pd.isna(last[c])]
        if missing:
            problems.append(f"indicadores de {tf} ainda indefinidos: {', '.join(missing)}")
    return problems


@dataclass
class TrendView:
    blocks_buy: bool
    factor: float  # fator de confirmação dos timeframes maiores (multiplica a confiança)
    notes_for: list[str] = field(default_factory=list)
    notes_against: list[str] = field(default_factory=list)
    block_reason: str | None = None


def higher_timeframes(ctx: DecisionContext, p: Estrategia) -> TrendView:
    """Filtro de tendência (1h/4h). Nunca é motivo de compra: só bloqueia ou reduz a confiança."""
    h4 = ctx.market.frames[p.tf_contexto].iloc[-1]
    h1 = ctx.market.frames[p.tf_confirmacao].iloc[-1]
    strong_down_1h = h1["adx"] >= p.adx_queda_forte_1h and h1["minus_di"] > h1["plus_di"]
    bearish_4h = h4["ema_50"] < h4["ema_200"] and h4["ema_50_slope"] < 0
    structure_down_1h = h1["structure"] == "baixa" and h1["close"] < h1["ema_50"]
    view = TrendView(False, 1.0)

    if strong_down_1h:
        view.blocks_buy = True
        view.block_reason = (
            f"queda forte no {p.tf_confirmacao}: ADX {h1['adx']:.0f} (>= {p.adx_queda_forte_1h:g}) com −DI {h1['minus_di']:.0f} > +DI {h1['plus_di']:.0f}"
        )
    elif bearish_4h and structure_down_1h:
        view.blocks_buy = True
        view.block_reason = (
            f"queda sustentada: EMA50 < EMA200 caindo no {p.tf_contexto} e topos/fundos descendentes abaixo da EMA50 no {p.tf_confirmacao}"
        )

    # confirmação: cada timeframe maior é favorável, neutro ou contra
    def state_4h() -> str:
        if bearish_4h:
            return "contra"
        return "favoravel" if h4["ema_50"] > h4["ema_200"] else "neutro"

    def state_1h() -> str:
        if h1["minus_di"] > h1["plus_di"] and h1["adx"] >= 20:
            return "contra"
        if h1["regime"] in ("lateral", "tendencia_alta"):
            return "favoravel"
        return "neutro"

    states = {p.tf_contexto: state_4h(), p.tf_confirmacao: state_1h()}
    for tf, st in states.items():
        if st == "favoravel":
            view.notes_for.append(f"{tf} favorável (sem tendência de baixa)")
        elif st == "contra":
            view.notes_against.append(f"{tf} com viés de baixa")
        else:
            view.notes_against.append(f"{tf} neutro (não confirma)")
    values = list(states.values())
    if "contra" in values:
        view.factor = p.confirmacao_um_contra
    elif "neutro" in values:
        view.factor = p.confirmacao_um_neutro
    else:
        view.factor = p.confirmacao_ambos_favoraveis
    return view


@dataclass(frozen=True)
class Levels:
    """Níveis no par de ANÁLISE (o painel traduz para o par de operação)."""

    entry: float
    stop: float
    target1: float
    target2: float
    reward_net_pct: float  # ganho até o alvo 1, já descontado o custo de ida e volta
    risk_net_pct: float  # perda até o stop, somando o custo de ida e volta
    rr: float  # reward_net / risk_net
    target_distance_pct: float  # distância bruta até o alvo 1
    size_brl: float | None = None  # tamanho sugerido para que, no stop, a perda = risco por operação
    target1_source: str = ""


def buy_levels(ctx: DecisionContext, p: Estrategia) -> Levels | None:
    s15 = ctx.market.frames[p.tf_setup].iloc[-1]
    g5 = ctx.market.frames[p.tf_gatilho]
    entry = ctx.market.price
    atr5 = float(g5["atr"].iloc[-1])
    recent_low = float(g5["low"].iloc[-p.stop_fundo_barras_5m :].min())
    stop = min(recent_low - p.stop_atr_5m * atr5, entry - p.stop_min_atr_5m * atr5)

    # alvo 1 = a média mais próxima ACIMA da entrada (VWAP do dia ou meio das bandas)
    means = [(float(s15["vwap"]), "VWAP do dia"), (float(s15["bb_mid"]), f"meio das bandas ({p.tf_setup})")]
    above = [(m, src) for m, src in means if m > entry]
    if not above:
        return None  # preço já está acima das médias: não há reversão a buscar
    target1, src = min(above)
    _, res = nearest_levels(ctx.market.levels, entry)
    if res is not None and entry < res.price < target1:
        target1, src = res.price, f"resistência antes da média ({res.source})"
    target2 = max(float(s15["bb_upper"]), target1)

    cost = ctx.cost_pct
    dist = (target1 / entry - 1) * 100
    reward = dist - cost
    risk = (1 - stop / entry) * 100 + cost
    rr = reward / risk if risk > 0 else 0.0
    size = None
    if ctx.risk.risk_per_trade_brl and risk > 0:
        size = ctx.risk.risk_per_trade_brl / (risk / 100)
        if ctx.risk.capital_brl:
            size = min(size, ctx.risk.capital_brl)
    return Levels(entry, stop, target1, target2, reward, risk, rr, dist, size, src)


def exit_check(ctx: DecisionContext, p: Estrategia, *, ratio: float = 1.0) -> tuple[str, str, float] | None:
    """Com posição aberta: (motivo curto, explicação, confiança) se alguma regra de saída disparou.

    `ratio` só afeta os TEXTOS (preços mostrados no par de operação); as comparações usam o par de análise.
    """
    pos = ctx.position
    if pos is None:
        return None
    price = ctx.market.price
    tp = lambda x: num(x * ratio)
    s15 = ctx.market.frames[p.tf_setup].iloc[-1]
    g5 = ctx.market.frames[p.tf_gatilho].iloc[-1]
    if pos.stop is not None and price <= pos.stop:
        return "stop", f"preço {tp(price)} atingiu o stop {tp(pos.stop)}: sair para limitar a perda", 100.0
    if pos.target2 is not None and price >= pos.target2:
        return "alvo2", f"preço {tp(price)} atingiu o alvo 2 {tp(pos.target2)}", 95.0
    if pos.target1 is not None and price >= pos.target1:
        return "alvo1", f"preço {tp(price)} atingiu o alvo 1 {tp(pos.target1)} (volta à média)", 90.0
    if s15["bb_pctb"] >= p.vender_pctb and g5["rsi"] > p.vender_rsi_5m and s15["dist_vwap_atr"] >= p.vender_vwap_atr:
        return (
            "esticado_cima",
            f"esticado para cima: %B {num(s15['bb_pctb'], 2)}, RSI {p.tf_gatilho} {num(g5['rsi'], 0)}, {num(s15['dist_vwap_atr'], 1)} ATR acima do VWAP",
            80.0,
        )
    trend = higher_timeframes(ctx, p)
    if trend.blocks_buy:
        return "invalidacao", f"leitura invalidada: {trend.block_reason}", 85.0
    held_min = (ctx.market.time_ms - pos.opened_ms) / 60_000
    if held_min >= p.tempo_max_min:
        return "tempo", f"posição aberta há {held_min:.0f} min sem chegar ao alvo (limite {p.tempo_max_min:.0f} min): reversão que não acontece rápido costuma não acontecer", 75.0
    return None


POSITION_STOP_ATR_5M = 2.0  # posição sem sinal associado: stop 2 ATR(5m) abaixo do custo médio


def rules_levels_for_position(ctx: DecisionContext, p: Estrategia) -> tuple[float, float, float]:
    """Stop e alvos para uma posição SEM sinal associado, calculados pelas regras a partir do custo médio.

    Alvo 1 = média (VWAP/meio das bandas) acima do custo; se não houver, custo + 2 × custo de ida e volta.
    """
    pos = ctx.position
    assert pos is not None
    s15 = ctx.market.frames[p.tf_setup].iloc[-1]
    atr5 = float(ctx.market.frames[p.tf_gatilho]["atr"].iloc[-1])
    stop = pos.avg_cost - POSITION_STOP_ATR_5M * atr5
    above = [m for m in (float(s15["vwap"]), float(s15["bb_mid"])) if m > pos.avg_cost]
    t1 = min(above) if above else pos.avg_cost * (1 + 2 * ctx.cost_pct / 100)
    t2 = max(float(s15["bb_upper"]), t1)
    return stop, t1, t2

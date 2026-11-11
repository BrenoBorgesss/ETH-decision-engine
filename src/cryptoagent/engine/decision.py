"""decide(): COMPRAR / VENDER / ESPERAR, com confiança calculada às claras.

Ordem:
1. Travas gerais (dados, conta, sessão, perda do dia, spread, incerteza). Qualquer uma -> ESPERAR.
2. Com posição aberta: regras de saída (stop, alvos, esticado para cima, invalidação, tempo).
3. Sem posição: regime, filtro de tendência, sinais, gatilho anti-faca, níveis, distância
   mínima até o alvo, risco/retorno. Tudo passou e a confiança >= mínimo do regime -> COMPRAR.

Confiança = nota dos sinais × fator dos timeframes maiores × qualidade dos dados × fator do horário.
Cada parcela aparece no resultado, com a explicação.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from cryptoagent import DISCLAIMER
from cryptoagent.config import Estrategia
from cryptoagent.engine.context import DecisionContext
from cryptoagent.engine.rules import Levels, buy_levels, data_problems, exit_check, higher_timeframes, rules_levels_for_position
from cryptoagent.engine.signals import SignalScore, TriggerResult, anti_knife_trigger, score_signals
from cryptoagent.fmt import num, pct

# Saídas que reduzem risco: as únicas indicadas depois de atingida a perda máxima do dia.
RISK_REDUCING_EXITS = ("stop", "invalidacao", "tempo")

REGIME_PT = {
    "tendencia_alta": "tendência de alta",
    "tendencia_baixa": "tendência de baixa",
    "lateral": "lateral",
    "alta_volatilidade": "alta volatilidade",
    "transicao": "transição",
    "indefinido": "indefinido",
}


@dataclass(frozen=True)
class ConfidencePart:
    name: str
    value: float
    explanation: str


@dataclass
class Decision:
    action: str  # COMPRAR | VENDER | ESPERAR
    confidence: float  # 0 a 100
    symbol: str
    trade_symbol: str
    time_ms: int
    price: float
    regime: str = "indefinido"
    parts: list[ConfidencePart] = field(default_factory=list)
    reasons_for: list[str] = field(default_factory=list)
    reasons_against: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)  # o que obrigou ESPERAR
    # Travas de ESTRATÉGIA (filtros da leitura técnica), por categoria. Também estão em `blocks`.
    # As de segurança (dados, conta, spread, perda do dia, falta de stop/alvo) nunca entram aqui.
    # Usado só pela variante de estudo do paper trading, que opera mesmo assim para medir cada trava.
    strategy_gates: dict[str, str] = field(default_factory=dict)
    signals: list[SignalScore] = field(default_factory=list)
    trigger: TriggerResult | None = None
    levels: Levels | None = None
    invalidation: list[str] = field(default_factory=list)
    exit_reason: str | None = None
    position_note: str | None = None
    disclaimer: str = DISCLAIMER


def _confidence(parts: list[ConfidencePart]) -> float:
    value = 100.0
    for part in parts:
        value *= part.value
    return max(0.0, min(100.0, value))


def _general_blocks(ctx: DecisionContext, p: Estrategia, problems: list[str]) -> tuple[list[str], float]:
    """Travas que valem com ou sem posição. Qualidade dos dados = 1 − `penalidade_por_aviso_dados` por aviso não crítico."""
    blocks = list(ctx.risk.blocked_reasons) + problems
    quality = max(0.0, 1.0 - p.penalidade_por_aviso_dados * len(ctx.risk.data_warnings))
    if quality < p.qualidade_min_dados:
        blocks.append(f"incerteza alta: qualidade dos dados {num(quality, 2)} < {num(p.qualidade_min_dados, 2)} ({'; '.join(ctx.risk.data_warnings)})")
    return blocks, quality


def decide(ctx: DecisionContext, p: Estrategia, *, spread_max_pct: float | None = None) -> Decision:
    m = ctx.market
    d = Decision("ESPERAR", 0.0, ctx.symbol, ctx.trade_symbol, m.time_ms, m.price)
    problems = data_problems(ctx, p)
    blocks, quality = _general_blocks(ctx, p, problems)
    if spread_max_pct is not None and m.spread_pct is not None and m.spread_pct > spread_max_pct:
        blocks.append(f"spread de {ctx.trade_symbol} em {pct(m.spread_pct, 3)} (limite {pct(spread_max_pct, 2)})")

    frames_ok = not problems
    if frames_ok:
        d.regime = str(m.frames[p.tf_setup].iloc[-1]["regime"])

    # ------------------------------------------------------- com posição aberta
    if ctx.position is not None:
        pos = ctx.position
        stop, t1, t2 = (pos.stop, pos.target1, pos.target2)
        if frames_ok and (stop is None or t1 is None):
            r_stop, r_t1, r_t2 = rules_levels_for_position(ctx, p)
            stop, t1, t2 = stop or r_stop, t1 or r_t1, t2 or r_t2
        ctx = _with_position_levels(ctx, stop, t1, t2)
        tp = lambda x: num(x * ctx.trade_price_ratio)  # textos sempre no par de OPERAÇÃO
        d.position_note = (
            f"posição de operação: {num(pos.qty, 6)} ETH a custo médio {tp(pos.avg_cost)} | stop {tp(stop)} | alvo 1 {tp(t1)} | alvo 2 {tp(t2)} ({pos.source})"
            if stop and t1 and t2
            else f"posição de operação: {num(pos.qty, 6)} ETH a custo médio {tp(pos.avg_cost)}"
        )
        if blocks:
            d.blocks = blocks + ([ctx.risk.daily_limit_reason] if ctx.risk.daily_limit_reason else [])
            d.reasons_against = ["há posição aberta: confira se a ordem de stop está na Binance (ela protege mesmo sem o agente)"]
            return d
        hit = exit_check(ctx, p, ratio=ctx.trade_price_ratio)
        if ctx.risk.daily_limit_reason:
            # Perda máxima atingida: só as saídas que REDUZEM risco viram VENDER; o resto é ESPERAR.
            if hit and hit[0] in RISK_REDUCING_EXITS:
                kind, text, conf = hit
                d.action, d.confidence, d.exit_reason = "VENDER", conf, kind
                d.parts = [ConfidencePart("regra de saída", conf / 100, text)]
                d.reasons_for = [text, "perda máxima do dia atingida: esta saída é permitida porque reduz o risco"]
                d.blocks = [ctx.risk.daily_limit_reason, "COMPRAR bloqueado até o próximo dia"]
                return d
            d.blocks = [ctx.risk.daily_limit_reason]
            d.reasons_against = ["há posição aberta: o stop na Binance (OCO) continua protegendo; o agente só indica VENDER em stop, invalidação ou tempo"]
            return d
        if hit:
            kind, text, conf = hit
            d.action, d.confidence, d.exit_reason = "VENDER", conf, kind
            d.parts = [ConfidencePart("regra de saída", conf / 100, text)]
            d.reasons_for = [text]
            d.reasons_against = ["vender realiza o resultado; conferir o preço na Binance antes"]
            return d
        d.reasons_for = ["manter a posição: nenhuma regra de saída disparou"]
        d.invalidation = [f"preço abaixo de {tp(stop)} (stop)", f"queda forte no {p.tf_confirmacao}", f"mais de {p.tempo_max_min:.0f} min sem chegar ao alvo"]
        return d

    # ------------------------------------------------------- sem posição: compra?
    # Mesmo com travas, a leitura técnica é calculada e mostrada (transparência); só a AÇÃO fica em ESPERAR.
    d.blocks = list(blocks) + ([ctx.risk.daily_limit_reason] if ctx.risk.daily_limit_reason else [])
    if not frames_ok:
        return d
    def gate(key: str, text: str) -> None:
        d.blocks.append(text)
        d.strategy_gates.setdefault(key, text)

    if d.regime in p.bloquear_regimes:
        gate("regime", f"regime {REGIME_PT.get(d.regime, d.regime)} no {p.tf_setup}: sinais de compra bloqueados")

    trend = higher_timeframes(ctx, p)
    combined, signals = score_signals(ctx, p)
    trigger = anti_knife_trigger(ctx, p)
    d.signals, d.trigger = signals, trigger
    d.parts = [
        ConfidencePart("sinais", combined, " + ".join(f"{s.name} {num(s.score, 2)}×{num(s.weight, 1)}" for s in signals if s.available) + " ÷ soma dos pesos"),
        ConfidencePart("timeframes maiores", trend.factor, "; ".join(trend.notes_for + trend.notes_against) or "-"),
        ConfidencePart("qualidade dos dados", quality, "sem avisos" if quality == 1 else "; ".join(ctx.risk.data_warnings)),
        ConfidencePart("horário", ctx.season.factor, "; ".join(ctx.season.reasons) or "sem efeito"),
    ]
    d.confidence = _confidence(d.parts)

    ranked = sorted((s for s in signals if s.available), key=lambda s: s.contribution, reverse=True)
    d.reasons_for = [s.detail for s in ranked if s.score > 0][:3] + trend.notes_for
    d.reasons_against = [s.detail for s in ranked if s.score == 0][:2] + trend.notes_against + [
        r for r in ctx.season.reasons if "×" in r and not r.endswith("(× 1)")
    ]
    if d.regime == "tendencia_baixa":
        d.reasons_against.append(f"regime de tendência de baixa no {p.tf_setup}: exige confiança maior")

    levels = buy_levels(ctx, p)
    d.levels = levels
    if trend.blocks_buy:
        gate("tendencia", f"filtro de tendência: {trend.block_reason}")
    if combined == 0:
        gate("esticado", "preço não está esticado para baixo em relação à média")
    if not trigger.ok:
        gate("gatilho", trigger.detail)
    if levels is None:
        d.blocks.append("preço já está acima das médias: não há volta à média para buscar")
    else:
        min_dist = p.distancia_alvo_min_custo * ctx.cost_pct
        if levels.target_distance_pct < min_dist:
            gate(
                "distancia_alvo",
                f"alvo 1 a só {pct(levels.target_distance_pct, 3)} (mínimo {pct(min_dist, 3)} = {num(p.distancia_alvo_min_custo, 1)} × custo de ida e volta de {pct(ctx.cost_pct, 3)})"
            )
        if levels.rr < p.rr_minimo:
            gate("risco_retorno", f"risco/retorno {num(levels.rr, 2)} depois das taxas (mínimo {num(p.rr_minimo, 1)})")
        d.invalidation = [
            f"candle de {p.tf_gatilho} fechando abaixo do stop {num(levels.stop * ctx.trade_price_ratio)}",
            f"{p.tf_confirmacao} virando queda forte (ADX >= {p.adx_queda_forte_1h:g} com −DI dominante)",
            f"não chegar ao alvo em {p.tempo_max_min:.0f} min",
            "notícia ou evento que faça a volatilidade disparar",
        ]
    min_conf = p.confianca_minima.get(d.regime, max(p.confianca_minima.values()))
    if d.confidence < min_conf:
        gate("confianca", f"confiança {num(d.confidence, 0)}% abaixo do mínimo de {min_conf:g}% para regime {REGIME_PT.get(d.regime, d.regime)}")

    if not d.blocks:
        d.action = "COMPRAR"
    return d


# Travas de CUSTO: são conta, não previsão (alvo que não paga as taxas perde mesmo acertando).
# A variante de estudo nunca as ignora; elas só aparecem no relatório como informação.
COST_GATES = ("distancia_alvo", "risco_retorno")


def study_entry_ok(d: Decision) -> bool:
    """Variante de estudo: compra virtual se SÓ travas de estratégia de previsão seguraram
    (nenhuma de segurança nem de custo) e há stop/alvo."""
    if d.action != "ESPERAR" or d.levels is None or not d.levels.size_brl or d.position_note is not None:
        return False
    if any(k in d.strategy_gates for k in COST_GATES):
        return False
    gates = set(d.strategy_gates.values())
    return all(b in gates for b in d.blocks)


def _with_position_levels(ctx: DecisionContext, stop: float | None, t1: float | None, t2: float | None) -> DecisionContext:
    assert ctx.position is not None
    return replace(ctx, position=replace(ctx.position, stop=stop, target1=t1, target2=t2))
